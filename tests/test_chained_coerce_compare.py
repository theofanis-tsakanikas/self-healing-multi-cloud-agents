"""
Regression for the GCP global_marketing self-heal flow (2026-06-18).

The architect's "replace negative or non-numeric with 0" rule for ad_spend produced
`pd.to_numeric(chunk['ad_spend'], errors='coerce').where(chunk['ad_spend'] >= 0, other=0)`.
The `.where` condition reads the ORIGINAL str/text column (ad_spend is MySQL text) before the
coerced value is assigned → `TypeError: Invalid comparison between dtype=str and int`. It PASSED
the validator (it's pd.to_numeric, not .astype(float)) and crashed only at CI runtime, wasting a
deploy. validate_generated_code must now flag the chained coerce+comparison at WRITE time so the
medic heals it locally — never burning CI minutes on it.

Three points of the flow are pinned:
  1. GENERATED form (`.astype(float)`)            → FAILS (existing astype check) → generation heal.
  2. HEALED form (two-statement pd.to_numeric)    → CLEAN (deploys, runs green at CI).
  3. The buggy chained `.where` form              → FAILS (the new check) → caught locally, not at CI.
"""
import os
import tempfile

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from agents.tools import validate_generated_code

# A complete GCP pipeline script that passes every OTHER validator check — only the ad_spend
# line varies between the three cases below (marked __AD_SPEND__).
_BASE = '''import os
import time
import datetime
import logging
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import create_engine
from trino.dbapi import connect as trino_connect
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from utils.cloud_config import cloud_get

_CLOUD = os.getenv("CLOUD_PROVIDER", "aws")
if _CLOUD == "aws":
    import boto3
elif _CLOUD == "gcp":
    from google.cloud import storage
elif _CLOUD == "azure":
    from azure.storage.blob import BlobServiceClient

logging.basicConfig(level=logging.INFO)


def run():
    logging.info("Pipeline starting: pipe_etl_gcp_pipeline_to_gcp")
    run_date = datetime.date.today().isoformat()
    destination_uri = os.getenv("DESTINATION_URI")
    partition_uri = f"{destination_uri}run_date={run_date}/"
    parsed = urlparse(partition_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip('/')

    if _CLOUD == "aws":
        s3 = boto3.client('s3')
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        if response.get('KeyCount', 0) > 0:
            return
    elif _CLOUD == "gcp":
        client = storage.Client()
        blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
        if blobs:
            return
    elif _CLOUD == "azure":
        container_name = bucket.split('@')[0]
        client = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
        container = client.get_container_client(container_name)
        if list(container.list_blobs(name_starts_with=prefix)):
            return

    if _CLOUD == "aws":
        host = cloud_get("aws", "db_host", db_type="postgres")
        user = cloud_get("aws", "db_user", db_type="postgres")
        pw = cloud_get("aws", "db_password", db_type="postgres")
        db = cloud_get("aws", "db_name", db_type="postgres")
        connection_string = f"postgresql+psycopg2://{user}:{pw}@{host}:5432/{db}"
    elif _CLOUD == "gcp":
        host = cloud_get("gcp", "db_host", db_type="mysql")
        user = cloud_get("gcp", "db_user", db_type="mysql")
        pw = cloud_get("gcp", "db_password", db_type="mysql")
        db = cloud_get("gcp", "db_name", db_type="mysql")
        connection_string = f"mysql+pymysql://{user}:{pw}@{host}:3306/{db}"
    elif _CLOUD == "azure":
        host = cloud_get("azure", "db_host", db_type="postgres")
        user = cloud_get("azure", "db_user", db_type="postgres")
        pw = cloud_get("azure", "db_password", db_type="postgres")
        db = cloud_get("azure", "db_name", db_type="postgres")
        connection_string = f"postgresql+psycopg2://{user}:{pw}@{host}:5432/{db}"

    start_time = time.time()
    total_rows = 0
    rejected_by_reason = {}
    query = "SELECT * FROM raw_global_marketing"

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):
            chunk['event_timestamp'] = pd.to_datetime(chunk['event_timestamp'], errors='coerce')

__AD_SPEND__

            _before = len(chunk)
            chunk = chunk[chunk['event_timestamp'] <= pd.Timestamp.now()]
            rejected_by_reason['temporal_validity'] = rejected_by_reason.get('temporal_validity', 0) + (_before - len(chunk))

            _before = len(chunk)
            chunk = chunk[~(chunk['campaign_id'].isnull() | (chunk['campaign_id'] == ''))]
            rejected_by_reason['completeness_enforcement'] = rejected_by_reason.get('completeness_enforcement', 0) + (_before - len(chunk))

            chunk['campaign_id'] = chunk['campaign_id'].where(chunk['campaign_id'].str.match(r'CMP-\\d{4}'), other='DEFAULT_BUCKET')
            chunk['is_suspicious'] = (chunk['clicks'] > chunk['impressions'])
            chunk['is_suspicious'] = chunk['is_suspicious'] | (chunk['clicks'] >= 1000000)

            int_cols = [c for c in chunk.select_dtypes(include='float64').columns
                        if any(kw in c.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
            for col in int_cols:
                chunk[col] = chunk[col].astype('Int64')

            chunk.to_parquet(
                f"{partition_uri}part_{i}.parquet",
                engine="pyarrow",
                compression="snappy",
                index=False,
                storage_options=dict()
            )
            total_rows += len(chunk)
    except Exception as e:
        logging.error(f"Pipeline failed: {e}")
        raise

    rejected_rows = sum(rejected_by_reason.values())
    duration_seconds = time.time() - start_time

    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    cursor.execute("CALL hive.system.sync_partition_metadata('marketing_global', 'pipe_etl_gcp_pipeline_to_gcp', 'ADD')")
    cursor.fetchall()

    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "unknown")
    registry = CollectorRegistry()
    Gauge('pipeline_rows_processed_total', 'd', ['project_id', 'cloud_provider'], registry=registry) \\
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(total_rows)
    Gauge('pipeline_rows_rejected_total', 'd', ['project_id', 'cloud_provider'], registry=registry) \\
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(rejected_rows)
    Gauge('pipeline_duration_seconds', 'd', ['project_id', 'cloud_provider'], registry=registry) \\
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(duration_seconds)
    push_to_gateway(pushgateway_url, job=project_id, registry=registry)


if __name__ == "__main__":
    run()
'''

_GENERATED_ASTYPE = (
    "            chunk['ad_spend'] = chunk['ad_spend'].astype(float)\n"
    "            chunk['ad_spend'] = chunk['ad_spend'].fillna(0).clip(lower=0)"
)
_HEALED_TWO_LINE = (
    "            chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce')\n"
    "            chunk['ad_spend'] = chunk['ad_spend'].fillna(0).clip(lower=0)"
)
_BUGGY_CHAINED = (
    "            chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce')"
    ".where(chunk['ad_spend'] >= 0, other=0)"
)


def _validate(ad_spend_line: str) -> str:
    d = tempfile.mkdtemp()
    f = os.path.join(d, "scripts", "pipe_etl_gcp_pipeline_to_gcp.py")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w") as fh:
        fh.write(_BASE.replace("__AD_SPEND__", ad_spend_line))
    return str(validate_generated_code.invoke({"filename": f}))


def test_generated_astype_is_flagged():
    out = _validate(_GENERATED_ASTYPE)
    assert "VALIDATION FAILED" in out
    assert "astype(float)" in out


def test_healed_two_statement_form_is_clean():
    out = _validate(_HEALED_TWO_LINE)
    assert "VALIDATION FAILED" not in out, out


def test_chained_coerce_compare_is_flagged():
    out = _validate(_BUGGY_CHAINED)
    assert "VALIDATION FAILED" in out
    assert "chained" in out.lower()
