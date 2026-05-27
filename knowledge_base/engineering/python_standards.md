# STANDARD: PYTHON DATA PIPELINES
All Python scripts generated for data engineering must follow these standards:

## MANDATORY SCRIPT STRUCTURE
Every pipeline script MUST follow this exact skeleton — imports, order of operations, and structure are non-negotiable:

```python
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
# Never import all three; unused cloud SDKs are not installed in the image.
_CLOUD = os.getenv("CLOUD_PROVIDER", "aws")
if _CLOUD == "aws":
    import boto3
elif _CLOUD == "gcp":
    from google.cloud import storage as gcs
elif _CLOUD == "azure":
    from azure.storage.blob import BlobServiceClient

logging.basicConfig(level=logging.INFO)


def run():
    logging.info("Pipeline starting: <pipeline_name>")  # ← FIRST line, before everything

    # 1. Idempotency check  (cloud-specific SDK selected above)
    # 2. DB engine + extraction loop (SAME try block)
    # 3. Trino partition registration
    # 4. Metrics emission


if __name__ == "__main__":
    run()
```

> **CRITICAL:** `import time` and `from utils.cloud_config import cloud_get` are ALWAYS required regardless of cloud. The cloud storage SDK (`boto3` / `google.cloud.storage` / `azure.storage.blob`) MUST be imported conditionally via the `_CLOUD` guard above — never import all three, and never hardcode `import boto3` unconditionally in a multi-cloud script.

---

## Connectivity
Use `sqlalchemy.create_engine`. Avoid raw DB-API drivers.

**Cloud-agnostic credential retrieval via `cloud_get()`** — reads from SSM first, then `.bootstrap_outputs.json`, then env vars. Never use `os.getenv()` directly for DB credentials:

```python
# PostgreSQL (AWS RDS)
host = cloud_get("aws", "rds_host")
port = cloud_get("aws", "rds_port") or "5432"
user = cloud_get("aws", "rds_username")
pw   = cloud_get("aws", "rds_password")
db   = cloud_get("aws", "rds_db_name")
connection_string = (
    f"postgresql+psycopg2://{user}:{pw}"
    f"@{host}:{port}/{db}"
)

# MySQL (GCP Cloud SQL)
host = cloud_get("gcp", "db_host")
port = cloud_get("gcp", "db_port") or "3306"
user = cloud_get("gcp", "db_user")
pw   = cloud_get("gcp", "db_password")
db   = cloud_get("gcp", "db_name")
connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"
```

The connection string MUST use **double quotes** for the outer f-string so that inner single-quoted calls do not cause a `SyntaxError: f-string: unmatched '('`. Never write `f'...{cloud_get('aws', 'key')}...'`.

- **Data Handling**: Use `pandas` for transformations.
- **Date Columns**: Always convert date/timestamp columns with `pd.to_datetime()` before any comparison. Database drivers often return date columns as strings. Always do: `chunk['col'] = pd.to_datetime(chunk['col'])` before filtering on dates.
- **Memory Management**: Always use `chunksize` in `pd.read_sql_query`. Always iterate with `enumerate()`: `for i, chunk in enumerate(pd.read_sql_query(..., chunksize=1000))`. This guarantees unique filenames (`part_{i}.parquet`) and index-aware logging.
- **Type Casting Before Write**: Before calling `to_parquet()`, cast integer/count/quantity columns from `float64` to `Int64` (nullable integer). Failing to do this causes Trino to read `double` instead of `BIGINT`:
```python
int_columns = [col for col in chunk.select_dtypes(include='float64').columns
               if any(kw in col.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
for col in int_columns:
    chunk[col] = chunk[col].astype('Int64')
```

---

## Storage
Use `pandas.to_parquet()` with **Hive-style date partitioning**. The subdirectory name MUST follow `run_date=YYYY-MM-DD` exactly — any other format will NOT be recognized as a partition by Trino:

```python
run_date = datetime.date.today().isoformat()
partition_uri = f"{destination_uri}run_date={run_date}/"

# CORRECT:  s3://bucket/processed/run_date=2026-05-05/
# WRONG:    s3://bucket/processed/2026-05-05/          ← ❌
# WRONG:    s3://bucket/processed/date=2026-05-05/     ← ❌

chunk.to_parquet(
    f"{partition_uri}part_{i}.parquet",
    engine="pyarrow",
    compression="snappy",
    index=False,
    storage_options={}
)
```

> **CRITICAL:** Do NOT add `run_date` as a column to the DataFrame. It is a Hive partition key derived from the S3 path — adding it inside the Parquet file causes a schema conflict.

> **Hidden dependency:** `s3fs` for `s3://` (AWS), `gcsfs` for `gs://` (GCP), `adlfs` for `abfs://` (Azure) — never imported directly but required at runtime by `pandas.to_parquet()`.

> **Hidden dependency:** `pyarrow` — never imported directly but required as the parquet engine. Omitting it causes `ImportError: pyarrow is required for parquet support`.

> **Hidden dependency:** `psycopg2-binary` for PostgreSQL. Never imported directly but required by SQLAlchemy at runtime.

---

## Idempotency Standard
Always wrap all pipeline logic inside a `run()` function. Check whether the destination partition already exists before writing — use the SDK matching the cloud provider:

```python
run_date = datetime.date.today().isoformat()
partition_uri = f"{destination_uri}run_date={run_date}/"
parsed = urlparse(partition_uri)
bucket = parsed.netloc
prefix = parsed.path.lstrip('/')
```

**AWS (boto3) — `import boto3` is REQUIRED at the top of the file:**
```python
s3 = boto3.client('s3')
response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
if response.get('KeyCount', 0) > 0:
    logging.info(f"Partition run_date={run_date} already populated. Skipping.")
    return
```

**GCP (google-cloud-storage):**
```python
from google.cloud import storage
client = storage.Client()
blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
if blobs:
    logging.info("Destination already populated. Skipping.")
    return
```

**Azure (azure-storage-blob):**
```python
from azure.storage.blob import BlobServiceClient
client = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
container = client.get_container_client(bucket)
blobs = list(container.list_blobs(name_starts_with=prefix))
if blobs:
    logging.info("Destination already populated. Skipping.")
    return
```

After the extraction loop, register the new partition with Trino so it is immediately queryable:
```python
trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
catalog, schema, table = "<catalog>", "<schema>", "<table>"  # from CATALOG_AND_MONITORING.trino_metadata
conn = trino_connect(host=trino_host, port=8080, user="pipeline")
cursor = conn.cursor()
cursor.execute(f"CALL {catalog}.system.sync_partition_metadata('{schema}', '{table}', 'ADD')")
cursor.fetchall()
logging.info(f"Trino partition run_date={run_date} registered.")
```

> **Explicit dependency:** `trino` MUST be in `requirements.txt`. This is the most commonly omitted dependency — the script will crash with `ModuleNotFoundError` without it.

## Requirements Standard
The `requirements.txt` MUST contain exactly these packages (add cloud-specific storage SDK based on `cloud_provider`):

```
pandas
sqlalchemy
psycopg2-binary
pyarrow
trino
prometheus-client
# AWS:
boto3
s3fs
# GCP:
# google-cloud-storage
# gcsfs
# Azure:
# azure-storage-blob
# adlfs
```

`trino` is always required regardless of cloud. `boto3`/`s3fs`, `google-cloud-storage`/`gcsfs`, or `azure-storage-blob`/`adlfs` are included only for the active cloud provider.
> **Explicit dependency:** `boto3` MUST be in `requirements.txt` for AWS pipelines.

---

## Error Handling Standard
`create_engine` AND the extraction loop MUST be inside the **SAME** `try` block:

```python
try:
    engine = create_engine(connection_string)
    for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):
        # transformations and write
except Exception as e:
    logging.error(f"Pipeline failed: {e}")
    raise
```

**CRITICAL: Do NOT put `create_engine` in a separate try/except and leave the loop unprotected.**

---

## Logging Standard
`logging.basicConfig` at module level. The start log MUST be the **first statement inside `run()`**, before the idempotency check:

```python
logging.basicConfig(level=logging.INFO)

def run():
    logging.info("Pipeline starting: <pipeline_name>")  # ← FIRST line

    total_rows = 0
    for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):
        # transformations...
        logging.info(f"Chunk {i}: {len(chunk)} rows processed")
        total_rows += len(chunk)

    logging.info(f"Pipeline completed. Total rows processed: {total_rows}")
```

---

## Metrics Emission Standard
Every pipeline script MUST push two Prometheus metrics after the extraction loop. `import time` is REQUIRED at the top of the file — `time.time()` is called here:

```python
# After logging "Pipeline completed..."
pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
project_id     = os.getenv("PROJECT_ID", "unknown")
cloud_provider = os.getenv("CLOUD_PROVIDER", "unknown")  # "aws", "azure", or "gcp"

registry = CollectorRegistry()
Gauge('pipeline_rows_processed_total', 'Total rows processed',
      ['project_id', 'cloud_provider'], registry=registry) \
    .labels(project_id=project_id, cloud_provider=cloud_provider).set(total_rows)
Gauge('pipeline_last_success_timestamp', 'Unix timestamp of last successful run',
      ['project_id', 'cloud_provider'], registry=registry) \
    .labels(project_id=project_id, cloud_provider=cloud_provider).set(time.time())

push_to_gateway(pushgateway_url, job=project_id, registry=registry)
logging.info(f"Metrics pushed to Pushgateway: rows={total_rows}, cloud={cloud_provider}")
```

Metrics MUST include both `project_id` AND `cloud_provider` labels — required for the cross-cloud Grafana dashboard to distinguish pipelines per cloud.

`CLOUD_PROVIDER` is injected by the Kubernetes Job manifest as a static env var. The Infra agent MUST add it to `job.yaml` alongside `PROJECT_ID`.

> **Explicit dependency:** `prometheus-client` MUST be in `requirements.txt`.

**CRITICAL:** Always use a fresh `CollectorRegistry()` — never the default global registry.

---

## Business Rule Translation Standard
Translate each `quality_standards` rule from the pipeline config to pandas code. Use `target_criteria` to identify the correct column(s) from the discovered schema.

| `on_failure_action`  | pandas implementation |
|----------------------|-----------------------|
| `DROP_RECORD`        | `df = df[condition]` |
| `EXCLUDE_AND_LOG`    | `excluded = df[~condition]; logging.warning(f'Excluded {len(excluded)} rows: <reason>'); df = df[condition]` |
| `DEFAULT_VALUE`      | `df[col] = df[col].where(condition, other=default)` |
| `FLAG_AS_SUSPICIOUS` | `df['is_suspicious'] = ~condition` |

**CRITICAL for FLAG_AS_SUSPICIOUS:** Do NOT add a filter after flagging — suspicious records must be retained with the flag set.

Apply all rules in sequence before any write. No rule may appear only as a comment.
