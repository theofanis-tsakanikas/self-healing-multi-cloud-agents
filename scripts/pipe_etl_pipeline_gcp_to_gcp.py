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
from google.cloud import storage

_CLOUD = os.getenv("CLOUD_PROVIDER", "gcp")

logging.basicConfig(level=logging.INFO)


def run():
    logging.info("Pipeline starting: pipe_etl_pipeline_gcp_to_gcp")  # ← MUST be the very first line

    # ── 1. IDEMPOTENCY CHECK ──────────────────────────────────────────────────
    run_date = datetime.date.today().isoformat()
    destination_uri = os.getenv("DESTINATION_URI")  # injected by K8s Job env
    partition_uri = f"{destination_uri}run_date={run_date}/"
    parsed = urlparse(partition_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip('/')

    client = storage.Client()
    blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
    if blobs:
        logging.info("Destination already populated. Skipping.")
        return

    # ── 2. CREDENTIALS via cloud_get() ───────────────────────────────────────
    if _CLOUD == "aws":
        host = cloud_get("aws", "db_host", db_type="mysql")
        port = cloud_get("aws", "db_port", db_type="mysql") or "3306"
        user = cloud_get("aws", "db_user", db_type="mysql")
        pw   = cloud_get("aws", "db_password", db_type="mysql")
        db   = cloud_get("aws", "db_name", db_type="mysql")
        connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"
    elif _CLOUD == "gcp":
        host = cloud_get("gcp", "db_host", db_type="mysql")
        port = cloud_get("gcp", "db_port", db_type="mysql") or "3306"
        user = cloud_get("gcp", "db_user", db_type="mysql")
        pw   = cloud_get("gcp", "db_password", db_type="mysql")
        db   = cloud_get("gcp", "db_name", db_type="mysql")
        connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"
    elif _CLOUD == "azure":
        host = cloud_get("azure", "db_host", db_type="mysql")
        port = cloud_get("azure", "db_port", db_type="mysql") or "3306"
        user = cloud_get("azure", "db_user", db_type="mysql")
        pw   = cloud_get("azure", "db_password", db_type="mysql")
        db   = cloud_get("azure", "db_name", db_type="mysql")
        connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"

    # ── 3. EXTRACTION + TRANSFORMATION + WRITE (one try block) ───────────────
    start_time = time.time()   # for pipeline_duration_seconds metric
    total_rows = 0
    rejected_by_reason = {}    # rule_name → cumulative dropped rows (one entry per row-removing rule)
    query = "SELECT * FROM raw_global_marketing"  # replace with actual table from context

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):
            # 3a. Date conversion
            chunk['event_timestamp'] = pd.to_datetime(chunk['event_timestamp'], errors='coerce')

            # 3b. Business rules
            # Rule 1: Ensure ad-spend metrics are non-negative for accounting.
            chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce')
            _before = len(chunk)
            chunk['ad_spend'] = chunk['ad_spend'].fillna(0).clip(lower=0)
            rejected_by_reason['monetary_integrity'] = rejected_by_reason.get('monetary_integrity', 0) + (_before - len(chunk))

            # Rule 2: Prevent future-dated events from entering hourly reports.
            _before = len(chunk)
            _mask = chunk['event_timestamp'] > pd.Timestamp.now()
            logging.warning(f"Excluded {_mask.sum()} future-dated rows (temporal_validity).")
            chunk = chunk[~_mask]
            rejected_by_reason['temporal_validity'] = rejected_by_reason.get('temporal_validity', 0) + (_before - len(chunk))

            # Rule 3: Ensure every log is attributed to a campaign.
            _before = len(chunk)
            chunk = chunk[~chunk['campaign_id'].isnull()]
            rejected_by_reason['completeness_enforcement'] = rejected_by_reason.get('completeness_enforcement', 0) + (_before - len(chunk))

            # Rule 4: Align unknown campaign identifiers to a default bucket.
            chunk['campaign'] = chunk['campaign'].where(chunk['campaign'].str.match(r'CMP-\d{4}'), other='DEFAULT_BUCKET')

            # Rule 5: Flags impossible engagement (CTR > 100%) for downstream review.
            chunk['is_suspicious'] = (chunk['clicks'] > chunk['impressions'])

            # Rule 6: Flags extreme click outliers that suggest bot traffic or technical glitches.
            chunk['is_suspicious'] = chunk['is_suspicious'] | (chunk['clicks'] >= 1000000)

            # 3c. Type casting
            int_cols = [c for c in chunk.select_dtypes(include='float64').columns
                        if any(kw in c.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
            for col in int_cols:
                chunk[col] = chunk[col].astype('Int64')

            # 3d. Write
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
    duration_seconds = time.time() - start_time   # for pipeline_duration_seconds metric
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    cursor.execute("CALL hive.system.sync_partition_metadata('marketing_global', 'pipe_etl_pipeline_gcp_to_gcp', 'ADD')")
    logging.info(f"Trino partition run_date={run_date} registered.")

    # ── 5. METRICS EMISSION ───────────────────────────────────────────────────
    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id     = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "gcp")

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