import os
import time
import datetime
import logging
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import create_engine
from trino.dbapi import connect as trino_connect
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from utils.cloud_config import cloud_get  # SSM → bootstrap_outputs → env fallback

_CLOUD = os.getenv("CLOUD_PROVIDER", "gcp")

logging.basicConfig(level=logging.INFO)

def run():
    logging.info("Pipeline starting: pipe_mkt_global_to_gcp")

    # ── 1. IDEMPOTENCY CHECK ──────────────────────────────────────────────────
    run_date = datetime.date.today().isoformat()
    destination_uri = os.getenv("DESTINATION_URI")  # injected by K8s Job env
    partition_uri = f"{destination_uri}run_date={run_date}/"
    parsed = urlparse(partition_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip('/')

    if _CLOUD == "gcp":
        from google.cloud import storage
        client = storage.Client()
        blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
        if blobs:
            logging.info("Destination already populated. Skipping.")
            return

    # ── 2. CREDENTIALS via cloud_get() ───────────────────────────────────────
    host = cloud_get("gcp", "db_host", db_type="mysql")
    port = cloud_get("gcp", "db_port", db_type="mysql") or "3306"
    user = cloud_get("gcp", "db_user", db_type="mysql")
    pw   = cloud_get("gcp", "db_password", db_type="mysql")
    db   = cloud_get("gcp", "db_name", db_type="mysql")
    connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"

    # ── 3. EXTRACTION + TRANSFORMATION + WRITE (one try block) ───────────────
    start_time = time.time()   # for pipeline_duration_seconds metric
    total_rows = 0
    rejected_by_reason = {}    # rule_name → cumulative dropped rows
    query = "SELECT * FROM raw_global_marketing"

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):

            # Business rules implementation
            _before = len(chunk)
            chunk = chunk[chunk['ad_spend'].astype(float) >= 0.0]
            rejected_by_reason['spend_integrity'] = rejected_by_reason.get('spend_integrity', 0) + (_before - len(chunk))

            _before = len(chunk)
            chunk = chunk[chunk['event_timestamp'] <= pd.Timestamp.now()]
            rejected_by_reason['temporal_validity'] = rejected_by_reason.get('temporal_validity', 0) + (_before - len(chunk))

            _before = len(chunk)
            chunk = chunk.dropna(subset=['campaign_id'])
            rejected_by_reason['completeness_enforcement'] = rejected_by_reason.get('completeness_enforcement', 0) + (_before - len(chunk))

            chunk['campaign_id'] = chunk['campaign_id'].where(chunk['campaign_id'].str.match(r'^CMP-\d{4}$'), other='UNASSIGNED_CAMPAIGN')

            chunk['is_suspicious'] = (chunk['clicks'] > chunk['impressions']) | (chunk['clicks'] >= 1000000)

            # Type casting
            int_cols = [c for c in chunk.select_dtypes(include='float64').columns if any(kw in c.lower() for kw in ['clicks', 'impressions'])]
            for col in int_cols:
                chunk[col] = chunk[col].astype('Int64')

            # Write to parquet
            chunk.to_parquet(f"{partition_uri}part_{i}.parquet", engine="pyarrow", compression="snappy", index=False, storage_options=dict())
            logging.info(f"Chunk {i}: {len(chunk)} rows processed")
            total_rows += len(chunk)

    except Exception as e:
        logging.error(f"Pipeline failed: {e}")
        raise

    rejected_rows = sum(rejected_by_reason.values())
    duration_seconds = time.time() - start_time   # for pipeline_duration_seconds metric
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    schema, table = "marketing_global", "pipe_mkt_global_to_gcp"  # from CATALOG_AND_MONITORING.trino_metadata
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    cursor.execute(f"CALL hive.system.sync_partition_metadata('{schema}', '{table}', 'ADD')")
    cursor.fetchall()
    logging.info(f"Trino partition run_date={run_date} registered.")

    # ── 5. METRICS EMISSION ───────────────────────────────────────────────────
    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "gcp")

    # Emit metrics
    registry = CollectorRegistry()
    Gauge('pipeline_rows_processed_total', 'Total rows written to storage after business rules', ['project_id', 'cloud_provider'], registry=registry).labels(project_id=project_id, cloud_provider=cloud_provider).set(total_rows)
    Gauge('pipeline_last_success_timestamp', 'Unix timestamp of last successful run', ['project_id', 'cloud_provider'], registry=registry).labels(project_id=project_id, cloud_provider=cloud_provider).set(time.time())
    Gauge('pipeline_rows_rejected_total', 'Rows removed by DROP_RECORD / EXCLUDE_AND_LOG rules', ['project_id', 'cloud_provider'], registry=registry).labels(project_id=project_id, cloud_provider=cloud_provider).set(rejected_rows)
    Gauge('pipeline_duration_seconds', 'Wall-clock duration of the extract-transform-write phase', ['project_id', 'cloud_provider'], registry=registry).labels(project_id=project_id, cloud_provider=cloud_provider).set(duration_seconds)

    rejected_by_reason_gauge = Gauge('pipeline_rows_rejected_by_reason', 'Rows rejected per business rule, labelled by rule name', ['project_id', 'cloud_provider', 'reason'], registry=registry)
    for _reason, _count in rejected_by_reason.items():
        rejected_by_reason_gauge.labels(project_id=project_id, cloud_provider=cloud_provider, reason=_reason).set(_count)

    push_to_gateway(pushgateway_url, job=project_id, registry=registry)
    logging.info(f"Metrics pushed: rows={total_rows}, rejected={rejected_rows}, duration={duration_seconds:.1f}s, cloud={cloud_provider}")

if __name__ == "__main__":
    run()