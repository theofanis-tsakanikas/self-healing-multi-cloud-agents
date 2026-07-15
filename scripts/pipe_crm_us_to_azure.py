import os
import time
import datetime
import logging
from urllib.parse import urlparse
import hashlib

import pandas as pd
from sqlalchemy import create_engine
from trino.dbapi import connect as trino_connect
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from utils.cloud_config import cloud_get  # SSM → bootstrap_outputs → env fallback

_CLOUD = os.getenv("CLOUD_PROVIDER", "aws")
if _CLOUD == "aws":
    import boto3
elif _CLOUD == "gcp":
    from google.cloud import storage
elif _CLOUD == "azure":
    from azure.storage.blob import BlobServiceClient

logging.basicConfig(level=logging.INFO)

def run():
    logging.info("Pipeline starting: pipe_crm_us_to_azure")

    # ── 1. IDEMPOTENCY CHECK ──────────────────────────────────────────────────
    run_date = datetime.date.today().isoformat()
    destination_uri = os.getenv("DESTINATION_URI")  # injected by K8s Job env
    partition_uri = f"{destination_uri}run_date={run_date}/"
    parsed = urlparse(partition_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip('/')

    if _CLOUD == "aws":
        s3 = boto3.client('s3')
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        if response.get('KeyCount', 0) > 0:
            logging.info(f"Partition run_date={run_date} already populated. Skipping.")
            return
    elif _CLOUD == "gcp":
        client = storage.Client()
        blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
        if blobs:
            logging.info("Destination already populated. Skipping.")
            return
    elif _CLOUD == "azure":
        container_name = bucket.split('@')[0]
        client = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
        container = client.get_container_client(container_name)
        blobs = list(container.list_blobs(name_starts_with=prefix))
        if blobs:
            logging.info("Destination already populated. Skipping.")
            return

    # ── 2. CREDENTIALS via cloud_get() ───────────────────────────────────────
    if _CLOUD == "aws":
        host = cloud_get("aws", "db_host", db_type="postgres")
        port = cloud_get("aws", "db_port", db_type="postgres") or "5432"
        user = cloud_get("aws", "db_user", db_type="postgres")
        pw = cloud_get("aws", "db_password", db_type="postgres")
        db = cloud_get("aws", "db_name", db_type="postgres")
        connection_string = (
            f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"
        )
    elif _CLOUD == "gcp":
        host = cloud_get("gcp", "db_host", db_type="mysql")
        port = cloud_get("gcp", "db_port", db_type="mysql") or "3306"
        user = cloud_get("gcp", "db_user", db_type="mysql")
        pw = cloud_get("gcp", "db_password", db_type="mysql")
        db = cloud_get("gcp", "db_name", db_type="mysql")
        connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"
    elif _CLOUD == "azure":
        host = cloud_get("azure", "db_host", db_type="postgres")
        port = cloud_get("azure", "db_port", db_type="postgres") or "5432"
        user = cloud_get("azure", "db_user", db_type="postgres")
        pw = cloud_get("azure", "db_password", db_type="postgres")
        db = cloud_get("azure", "db_name", db_type="postgres")
        connection_string = f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"

    # ── 3. EXTRACTION + TRANSFORMATION + WRITE (one try block) ───────────────
    start_time = time.time()   # for pipeline_duration_seconds metric
    total_rows = 0
    rejected_by_reason = {}
    query = "SELECT * FROM raw_us_crm"

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):
            # PII Anonymization
            chunk['full_name'] = chunk['full_name'].apply(lambda v: hashlib.sha256(str(v).encode()).hexdigest())
            chunk['email_address'] = chunk['email_address'].str.replace(r'(?<=.).*?(?=@)', '***', regex=True)
            chunk['phone_number'] = chunk['phone_number'].astype(str).str.replace(r'\d(?=\d{4})', '*', regex=True)

            # Business Rules
            # 1. contact_format_integrity
            _mask = ~chunk['email_address'].str.contains('@')
            logging.warning(f"Excluded {_mask.sum()} rows: Invalid email format.")
            chunk = chunk[~_mask]

            # 2. mandatory_contact_info
            _before = len(chunk)
            chunk = chunk.dropna(subset=['email_address', 'phone_number'], how='all')
            rejected_by_reason['mandatory_contact_info'] = rejected_by_reason.get('mandatory_contact_info', 0) + (_before - len(chunk))

            # 3. entity_uniqueness
            _mask = chunk.duplicated(subset=['cust_id'], keep=False)
            chunk['is_suspicious'] = _mask

            # Type casting
            int_cols = [c for c in chunk.select_dtypes(include='float64').columns
                        if any(kw in c.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
            for col in int_cols:
                chunk[col] = chunk[col].astype('Int64')

            # Write
            chunk.to_parquet(
                f"{partition_uri}part_{i}.parquet",
                engine="pyarrow",
                compression="snappy",
                index=False,
                storage_options=dict()
            )
            logging.info(f"Chunk {i}: {len(chunk)} rows processed")
            total_rows += len(chunk)

    except Exception as e:
        logging.error(f"Pipeline failed: {e}")
        raise

    rejected_rows = sum(rejected_by_reason.values())
    duration_seconds = time.time() - start_time
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    for _attempt in range(5):
        try:
            cursor.execute("CALL hive.system.sync_partition_metadata('crm_us', 'pipe_crm_us_to_azure', 'ADD')")
            cursor.fetchall()
            break
        except Exception as _e:
            if "not found" in str(_e).lower() and _attempt < 4:
                logging.warning(f"Trino table not visible yet (attempt {_attempt + 1}/5) — retrying in 3s.")
                time.sleep(3)
                continue
            raise
    logging.info(f"Trino partition run_date={run_date} registered.")

    # ── 5. METRICS EMISSION ───────────────────────────────────────────────────
    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "unknown")

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