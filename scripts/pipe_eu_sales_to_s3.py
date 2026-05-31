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

# Cloud-specific storage SDK — import ONLY the one matching CLOUD_PROVIDER.
_CLOUD = os.getenv("CLOUD_PROVIDER", "aws")
if _CLOUD == "aws":
    import boto3
elif _CLOUD == "gcp":
    from google.cloud import storage as gcs
elif _CLOUD == "azure":
    from azure.storage.blob import BlobServiceClient

logging.basicConfig(level=logging.INFO)

def run():
    logging.info("Pipeline starting: EU Sales to S3")  # ← MUST be the very first line

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
        client = gcs.Client()
        blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
        if blobs:
            logging.info("Destination already populated. Skipping.")
            return
    elif _CLOUD == "azure":
        client = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
        container = client.get_container_client(bucket)
        blobs = list(container.list_blobs(name_starts_with=prefix))
        if blobs:
            logging.info("Destination already populated. Skipping.")
            return

    # ── 2. CREDENTIALS via cloud_get() ───────────────────────────────────────
    if _CLOUD == "aws":
        host = cloud_get("aws", "db_host",     db_type="postgres")
        port = cloud_get("aws", "db_port",     db_type="postgres") or "5432"
        user = cloud_get("aws", "db_user",     db_type="postgres")
        pw   = cloud_get("aws", "db_password", db_type="postgres")
        db   = cloud_get("aws", "db_name",     db_type="postgres")
        connection_string = (
            f"postgresql+psycopg2://{user}:{pw}"
            f"@{host}:{port}/{db}"
        )
    elif _CLOUD == "gcp":
        host = cloud_get("gcp", "db_host")
        port = cloud_get("gcp", "db_port") or "3306"
        user = cloud_get("gcp", "db_user")
        pw   = cloud_get("gcp", "db_password")
        db   = cloud_get("gcp", "db_name")
        connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"
    elif _CLOUD == "azure":
        host = cloud_get("azure", "db_host")
        port = cloud_get("azure", "db_port") or "1433"
        user = cloud_get("azure", "db_user")
        pw   = cloud_get("azure", "db_password")
        db   = cloud_get("azure", "db_name")
        connection_string = f"mssql+pyodbc://{user}:{pw}@{host}:{port}/{db}?driver=ODBC+Driver+18+for+SQL+Server"

    # ── 3. EXTRACTION + TRANSFORMATION + WRITE (one try block) ───────────────
    start_time = time.time()   # for pipeline_duration_seconds metric
    total_rows = 0
    rejected_rows = 0          # rows removed by DROP_RECORD / EXCLUDE_AND_LOG rules
    query = "SELECT * FROM raw_eu_sales"  # source table from context

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):

            # 3a. Date conversion — ALWAYS before any date comparison
            chunk['order_date'] = pd.to_datetime(chunk['order_date'])

            # 3b. Business rules — translate ALL quality_standards from pipeline config.
            _rows_before = len(chunk)
            chunk = chunk[chunk['unit_price'] > 0.0]  # monetary_integrity
            _future = chunk['order_date'] > pd.Timestamp.now()  # temporal_validity
            if _future.any():
                logging.warning(f"Excluded {_future.sum()} future-dated rows (temporal_validity).")
            chunk = chunk[~_future]
            chunk = chunk.dropna(subset=['order_id'])  # completeness_enforcement
            chunk['currency'] = chunk['currency'].where(chunk['currency'].isin(['EUR', 'GBP']), other='EUR')  # currency_standardization
            chunk['is_suspicious'] = (chunk['quantity'] >= 1000) | (chunk['quantity'] <= 0)  # volume_sanity_check + quantity_validity

            rejected_rows += _rows_before - len(chunk)

            # 3c. Type casting — cast float64 → Int64 for integer/count/quantity columns
            int_cols = [c for c in chunk.select_dtypes(include='float64').columns
                        if any(kw in c.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
            for col in int_cols:
                chunk[col] = chunk[col].astype('Int64')

            # 3d. Write — storage_options={} is MANDATORY
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

    duration_seconds = time.time() - start_time   # for pipeline_duration_seconds metric
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    catalog, schema, table = "hive", "sales_eu", "pipe_eu_sales_to_s3"  # from CATALOG_AND_MONITORING.trino_metadata
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    cursor.execute(f"CALL {catalog}.system.sync_partition_metadata('{schema}', '{table}', 'ADD')")
    cursor.fetchall()
    logging.info(f"Trino partition run_date={run_date} registered.")

    # ── 5. METRICS EMISSION ───────────────────────────────────────────────────
    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id     = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "unknown")

    # Emit ALL FOUR metrics
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

    push_to_gateway(pushgateway_url, job=project_id, registry=registry)
    logging.info(
        f"Metrics pushed: rows={total_rows}, rejected={rejected_rows}, "
        f"duration={duration_seconds:.1f}s, cloud={cloud_provider}"
    )


if __name__ == "__main__":
    run()