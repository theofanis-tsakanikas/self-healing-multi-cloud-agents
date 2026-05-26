# STANDARD: PYTHON DATA PIPELINES
All Python scripts generated for data engineering must follow these standards:

- **Connectivity**: Use `sqlalchemy.create_engine`. Avoid raw DB-API drivers. The connection string MUST use **double quotes** for the outer f-string so that `os.getenv('VAR')` calls inside it (which use single quotes) do not cause a `SyntaxError: f-string: unmatched '('`:
```python
engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('POSTGRES_DB_USER')}:{os.getenv('POSTGRES_DB_PASSWORD')}"
    f"@{os.getenv('POSTGRES_DB_HOST')}:{os.getenv('POSTGRES_DB_PORT')}/{os.getenv('POSTGRES_DB_NAME')}"
)
```
Never write `f'...{os.getenv('VAR')}...'` — single-quoted f-strings with single-quoted inner calls are a syntax error in all Python versions.
- **Data Handling**: Use `pandas` for transformations.
- **Date Columns**: Always convert date/timestamp columns with `pd.to_datetime()` before any comparison. Database drivers (psycopg2, MySQL) often return date columns as strings. Comparing a string column against `pd.Timestamp` causes `TypeError: '>=' not supported between instances of 'Timestamp' and 'str'`. Always do: `chunk['col'] = pd.to_datetime(chunk['col'])` before filtering on dates.
- **Memory Management**: For large datasets, always use `chunksize` in `pd.read_sql_query`. Always iterate chunks with `enumerate()`: `for i, chunk in enumerate(pd.read_sql_query(..., chunksize=N))`. This guarantees unique filenames (`part_{i}.parquet`) and index-aware logging.
- **Type Casting Before Write**: Before calling `to_parquet()`, cast integer/count/quantity columns from `float64` (pandas default when NULLs exist) to `Int64` (nullable integer). Failing to do this causes Trino to read `double` instead of `BIGINT`, which breaks schema-declared `INTEGER`/`BIGINT` columns:
```python
int_columns = [col for col in chunk.select_dtypes(include='float64').columns
               if any(kw in col.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
for col in int_columns:
    chunk[col] = chunk[col].astype('Int64')
```
- **Environment**: Fetch credentials ONLY via `os.getenv()`. Never hardcode strings.
- **Storage**: Use `pandas.to_parquet()` for cloud writes with **Hive-style date partitioning**. Each run writes to a `run_date=YYYY-MM-DD/` subdirectory under the destination URI. The subdirectory name MUST follow the exact format `run_date=YYYY-MM-DD` — this is the Hive partition key format that Trino uses to discover partitions. Any other format (e.g. `EU_SALES-2026-05-05/`, `2026-05-05/`, `date=2026-05-05/`) will NOT be recognized as a partition and Trino will return empty results.

```python
import datetime
run_date = datetime.date.today().isoformat()  # e.g. "2026-05-05"

# CORRECT — Trino discovers this as a partition:
partition_uri = f"{destination_uri}run_date={run_date}/"
# e.g. "s3://eu-sales-insights-data/processed/run_date=2026-05-05/"

# WRONG — Trino ignores these:
# f"{destination_uri}EU_SALES-{run_date}/"   ← ❌
# f"{destination_uri}{run_date}/"             ← ❌
# f"{destination_uri}date={run_date}/"        ← ❌

chunk.to_parquet(
    f"{partition_uri}part_{i}.parquet",
    engine="pyarrow",
    compression="snappy",
    index=False,
    storage_options={}
)
```

> **CRITICAL:** Do NOT add `run_date` as a column to the DataFrame before writing. `run_date` is a Hive partition key — Trino derives its value from the S3 directory path (`run_date=YYYY-MM-DD/`). Adding it inside the Parquet file causes a schema conflict and will break Trino queries.
> **Hidden dependency:** `s3fs` MUST be added to `requirements.txt` for AWS S3 writes. It is never imported directly but is required at runtime by `pandas.to_parquet()` when the destination URI starts with `s3://`. Omitting it causes `ImportError: Install s3fs to access S3`.
> Similarly: `gcsfs` for `gs://` (GCP), `adlfs` for `abfs://` (Azure).

> **Explicit dependency:** `boto3` MUST be added to `requirements.txt`. It is used directly for the idempotency check (`boto3.client('s3')`) and is not pre-installed in `python:3.11-slim`.

> **Hidden dependency:** `psycopg2-binary` MUST be added to `requirements.txt` for any PostgreSQL connection via SQLAlchemy. It is never imported directly but SQLAlchemy requires it as the underlying DB-API driver at runtime. Omitting it causes `ModuleNotFoundError: No module named 'psycopg2'`.

> **Hidden dependency:** `pyarrow` MUST be added to `requirements.txt` whenever `pandas.to_parquet()` is used. It is never imported directly but pandas requires it as the parquet engine at runtime. Omitting it causes `ImportError: Import pyarrow failed. pyarrow is required for parquet support`.

## Logging Standard
Every pipeline script MUST follow this exact logging structure. `logging.basicConfig` goes at module level; the start log MUST be the **first statement inside `run()`**, before the idempotency check:

```python
logging.basicConfig(level=logging.INFO)

def run():
    logging.info("Pipeline starting: <pipeline_name>")  # ← FIRST line in run()
    # idempotency check follows here ...

    total_rows = 0
    for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=N)):
        # ... transformations ...
        logging.info(f"Chunk {i}: {len(chunk)} rows processed")
        total_rows += len(chunk)

    logging.info(f"Pipeline completed. Total rows processed: {total_rows}")
```

Do not omit the start log, the per-chunk row count, or the final total.

## Idempotency Standard
Before writing any chunk, check whether the destination already contains data. Use the SDK that matches the cloud provider — never mix providers.

Always wrap all pipeline logic (idempotency check + extraction loop) inside a `run()` function and call it via `if __name__ == "__main__"`. This allows early exit via `return` instead of `exit()`.

Extract `bucket` and `prefix` programmatically from the full destination URI using `urllib.parse` — never hardcode them as separate string literals. The idempotency check targets today's **date partition**, not the full parent prefix:
```python
import datetime
from urllib.parse import urlparse

run_date = datetime.date.today().isoformat()  # e.g. "2026-05-05"
partition_uri = f"{destination_uri}run_date={run_date}/"

parsed = urlparse(partition_uri)
bucket = parsed.netloc
prefix = parsed.path.lstrip('/')
```

**AWS (boto3):**
```python
s3 = boto3.client('s3')
response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
if response.get('KeyCount', 0) > 0:
    logging.info(f"Partition run_date={run_date} already populated. Skipping.")
    return
```

After the extraction loop completes, register the new partition with Trino so it is immediately queryable without a manual `MSCK REPAIR`:
```python
from trino.dbapi import connect as trino_connect

trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
catalog, schema, table = "<catalog>", "<schema>", "<table>"
conn = trino_connect(host=trino_host, port=8080, user="pipeline")
cursor = conn.cursor()
cursor.execute(
    f"CALL {catalog}.system.sync_partition_metadata('{schema}', '{table}', 'ADD')"
)
cursor.fetchall()
logging.info(f"Trino partition run_date={run_date} registered.")
```
Replace `<catalog>`, `<schema>`, `<table>` with the values from `CATALOG_AND_MONITORING.trino_metadata` in the context.

> **Explicit dependency:** `trino` (PyPI: `trino`) MUST be added to `requirements.txt`. It is the official Python client for Trino used to call `sync_partition_metadata`.

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

## Error Handling Standard
Wrap the DB engine creation and the extraction loop in try/except for graceful shutdown.
**CRITICAL: `create_engine` AND the extraction loop MUST be inside the SAME `try` block — one try/except covers both. Do NOT put `create_engine` in a separate try/except and leave the loop unprotected.**

```python
try:
    engine = create_engine(connection_string)
    for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=N)):
        # transformations and write
except Exception as e:
    logging.error(f"Pipeline failed: {e}")
    raise
```

## Business Rule Translation Standard
When a pipeline config defines `quality_standards`, each rule must be translated to pandas code as follows. Use `target_criteria` to identify the correct column(s) from the discovered schema.

| `on_failure_action`  | pandas implementation |
|----------------------|-----------------------|
| `DROP_RECORD`        | `df = df[condition]` |
| `EXCLUDE_AND_LOG`    | `excluded = df[~condition]; logging.warning(f'Excluded {len(excluded)} rows: <reason>'); df = df[condition]` |
| `DEFAULT_VALUE`      | `df[col] = df[col].where(condition, other=default)` |
| `FLAG_AS_SUSPICIOUS` | `df['is_suspicious'] = ~condition` |

**CRITICAL for FLAG_AS_SUSPICIOUS:** Adding the flag column is the ONLY operation. Do NOT add a filter line (`df = df[~df['is_suspicious']]`) after flagging — that would silently convert it into a DROP_RECORD. Suspicious records must be retained in the output with the flag set.

Apply all rules in sequence before any write operation. No rule may appear only as a comment.

## Metrics Emission Standard
Every pipeline script MUST push two Prometheus metrics to the Pushgateway after the extraction loop completes. This is what populates the Grafana dashboard.

Metrics MUST include **both** `project_id` AND `cloud_provider` labels. Without `cloud_provider`, the cross-cloud Grafana dashboard cannot distinguish EU Sales (AWS) from US CRM (Azure) or Global Marketing (GCP).

```python
import time
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

# Inside run(), after the extraction loop:
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

Place this block immediately after `logging.info(f"Pipeline completed. Total rows processed: {total_rows}")` and before the function ends.

`CLOUD_PROVIDER` is injected by the Kubernetes Job manifest as a static env var matching the `cloud_provider` field in the pipeline YAML (e.g. `"aws"`, `"azure"`, `"gcp"`). The Infra agent MUST add it to the `job.yaml` env block alongside `PROJECT_ID`.

> **Explicit dependency:** `prometheus-client` MUST be added to `requirements.txt`. It is the library used to push metrics to the Pushgateway.

**CRITICAL:** Use a fresh `CollectorRegistry()` for every push — never use the default global registry. A Job pod that runs once and exits cannot expose a `/metrics` endpoint, so the Pushgateway is the only valid approach for short-lived Kubernetes Jobs.
