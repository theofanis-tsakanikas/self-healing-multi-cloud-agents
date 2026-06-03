import os
import time
import datetime
import logging
import hashlib
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import create_engine
from trino.dbapi import connect as trino_connect
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
from utils.cloud_config import cloud_get  # SSM → bootstrap_outputs → env fallback

_CLOUD = os.getenv("CLOUD_PROVIDER", "azure")

logging.basicConfig(level=logging.INFO)

def run():
    logging.info("Pipeline starting: US CRM to Azure")

    # ── 1. IDEMPOTENCY CHECK ──────────────────────────────────────────────────
    run_date = datetime.date.today().isoformat()
    destination_uri = os.getenv("DESTINATION_URI")  # injected by K8s Job env
    partition_uri = f"{destination_uri}run_date={run_date}/"
    parsed = urlparse(partition_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip('/')

    # Check if the partition already exists
    if _CLOUD == "azure":
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
        container = client.get_container_client(bucket)
        blobs = list(container.list_blobs(name_starts_with=prefix))
        if blobs:
            logging.info(f"Partition run_date={run_date} already populated. Skipping.")
            return

    # ── 2. CREDENTIALS via cloud_get() ───────────────────────────────────────
    host = cloud_get("azure", "db_host", db_type="postgres")
    port = cloud_get("azure", "db_port", db_type="postgres") or "5432"
    user = cloud_get("azure", "db_user", db_type="postgres")
    pw   = cloud_get("azure", "db_password", db_type="postgres")
    db   = cloud_get("azure", "db_name", db_type="postgres")
    connection_string = f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"

    # ── 3. EXTRACTION + TRANSFORMATION + WRITE (one try block) ───────────────
    start_time = time.time()   # for pipeline_duration_seconds metric
    total_rows = 0
    rejected_by_reason = {}    # rule_name → cumulative dropped rows
    query = "SELECT * FROM raw_us_crm"

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):
            # PII Anonymization
            chunk['full_name'] = chunk['full_name'].apply(lambda v: hashlib.sha256(str(v).encode()).hexdigest())
            chunk['email_address'] = chunk['email_address'].str.replace(r'(?<=.).*?(?=@)', '***', regex=True)
            chunk['phone_number'] = chunk['phone_number'].astype(str).str.replace(r'\d(?=\d{4})', '*', regex=True)

            # 3b. Business rules — translate ALL quality_standards from pipeline config.
            rejected_by_reason = {}

            # Contact format integrity
            _before = len(chunk)
            chunk = chunk[chunk['email_address'].str.contains('@')]
            rejected_by_reason['contact_format_integrity'] = _before - len(chunk)

            # Mandatory contact info
            _before = len(chunk)
            chunk = chunk[chunk['email_address'].notnull() | chunk['phone_number'].notnull()]
            rejected_by_reason['mandatory_contact_info'] = _before - len(chunk)

            # Entity uniqueness
            _before = len(chunk)
            chunk['is_suspicious'] = chunk.duplicated(subset=['cust_id'], keep=False)
            rejected_by_reason['entity_uniqueness'] = _before - len(chunk)

            # 3c. Type casting — cast float64 → Int64 for integer/count/quantity columns
            int_cols = [c for c in chunk.select_dtypes(include='float64').columns
                        if any(kw in c.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
            for col in int_cols:
                chunk[col] = chunk[col].astype('Int64')

            # 3d. Write — storage_options={} is MANDATORY, do not omit it
            chunk.to_parquet(
                f"{partition_uri}part_{i}.parquet",
                engine="pyarrow",
                compression="snappy",
                index=False,
                storage_options={}
            )
            logging.info(f"Chunk {i}: {len(chunk)} rows processed")
            total_rows += len(chunk)

    except Exception as e:
        logging.error(f"Pipeline failed: {e}")
        raise

    # Scalar total DERIVED from the per-reason dict
    rejected_rows = sum(rejected_by_reason.values())
    duration_seconds = time.time() - start_time   # for pipeline_duration_seconds metric
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    catalog, schema, table = "azure_catalog", "crm_us", "pipe_crm_us_to_azure"
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    cursor.execute(f"CALL {catalog}.system.sync_partition_metadata('{schema}', '{table}', 'ADD')")
    cursor.fetchall()
    logging.info(f"Trino partition run_date={run_date} registered.")

    # ── 5. METRICS EMISSION ───────────────────────────────────────────────────
    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id     = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "unknown")

    # Emit ALL FIVE metrics
    registry = CollectorRegistry()
    Gauge('pipeline_rows_processed_total', 'Total rows written to storage after business rules',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(total_rows)
    Gauge('pipeline_last_success_timestamp', 'Unix timestamp of last successful run',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(time.time())
    Gauge('pipeline_rows_rejected_total', 'Rows removed by DROP_RECORD / EXCLUDE_AND_LOG rules',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(rejected_rows)
    Gauge('pipeline_duration_seconds', 'Wall-clock duration of the extract-transform-write phase',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(duration_seconds)

    # Per-rule breakdown
    rejected_by_reason_gauge = Gauge(
        'pipeline_rows_rejected_by_reason', 'Rows rejected per business rule, labelled by rule name',
        ['project_id', 'cloud_provider', 'reason'], registry=registry)
    for _reason, _count in rejected_by_reason.items():
        rejected_by_reason_gauge.labels(
            project_id=project_id, cloud_provider=cloud_provider, reason=_reason).set(_count)

    push_to_gateway(pushgateway_url, job=project_id, registry=registry)
    logging.info(
        f"Metrics pushed: rows={total_rows}, rejected={rejected_rows}, "
        f"by_reason={rejected_by_reason}, duration={duration_seconds:.1f}s, cloud={cloud_provider}"
    )


if __name__ == "__main__":
    run()