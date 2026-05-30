# STANDARD: PYTHON DATA PIPELINES
All Python scripts generated for data engineering must follow these standards exactly.
After writing any `.py` file, you MUST call `validate_generated_code` on it — if it returns errors, fix them before proceeding.

---

## CRITICAL RULES — read these BEFORE writing any code

These violations cause immediate runtime failure. No exceptions.

### Credentials
- `cloud_get()` is MANDATORY for all DB credentials. `os.getenv()` is FORBIDDEN for host/user/password/db — it bypasses SSM and returns None in production.
- Import: `from utils.cloud_config import cloud_get` — place after standard library imports, before cloud SDK block.
- Connection strings MUST use double-quoted outer f-strings to avoid `SyntaxError: f-string: unmatched '('`.
- Every `cloud_get()` call and the `connection_string` assignment MUST be inside a cloud-specific guard (`if _CLOUD == "aws":` / `elif _CLOUD == "gcp":` / `elif _CLOUD == "azure":`). An unguarded `cloud_get("aws", ...)` hardcodes AWS credentials into a supposedly cloud-agnostic script — it will fail silently when `CLOUD_PROVIDER=gcp` or `CLOUD_PROVIDER=azure` because the wrong credential keys are resolved.
```python
# ❌ WRONG — unguarded, breaks on GCP/Azure:
host = cloud_get("aws", "db_host", db_type="postgres")
connection_string = f"postgresql+psycopg2://..."

# ✅ CORRECT — each cloud block is self-contained:
if _CLOUD == "aws":
    host = cloud_get("aws", "db_host", db_type="postgres")
    connection_string = f"postgresql+psycopg2://..."
elif _CLOUD == "gcp":
    host = cloud_get("gcp", "db_host")
    connection_string = f"mysql+pymysql://..."
elif _CLOUD == "azure":
    host = cloud_get("azure", "db_host")
    connection_string = f"mssql+pyodbc://..."
```

### Storage
- `storage_options={}` is MANDATORY in every `to_parquet()` call — omitting it causes `TypeError` on cloud storage writes (s3://, gs://, abfss://).
- `run_date` MUST NOT be added as a DataFrame column — it is a Hive partition key derived from the path.
- Partition path format is always `run_date=YYYY-MM-DD/` — any other format breaks Trino partition discovery.
- `destination_uri` MUST come from `os.getenv("DESTINATION_URI")` — **never hardcode a URI string** (`"s3://..."`, `"gs://..."`, `"abfss://..."`). The K8s Job injects this at runtime; hardcoding it makes the script un-deployable to a different bucket without a code change.
```python
# ❌ WRONG — hardcoded, cannot be overridden at deploy time:
destination_uri = "s3://eu-sales-insights-data/processed/"

# ✅ CORRECT — injected by the K8s Job env block:
destination_uri = os.getenv("DESTINATION_URI")
```

### Error Handling
- `create_engine` AND the extraction loop MUST be in the **SAME** `try` block.
```python
# ❌ WRONG — engine unprotected:
engine = create_engine(connection_string)
try:
    for i, chunk in enumerate(...):

# ✅ CORRECT — both protected:
try:
    engine = create_engine(connection_string)
    for i, chunk in enumerate(...):
```

### Business Rules
- Every `quality_standards` entry from the pipeline config MUST be translated to real pandas code.
- **`is_suspicious` is a conditional column — not a default:**
  - `FLAG_AS_SUSPICIOUS` rule present → `chunk['is_suspicious'] = ~condition`. Do NOT filter rows after — retain all. Multiple rules combine with `|`.
  - No `FLAG_AS_SUSPICIOUS` rule → omit `is_suspicious` entirely. No column, no placeholder.
- **`chunk['is_suspicious'] = False` is a COMPLIANCE VIOLATION** — never a valid implementation regardless of context.

**Mapping algorithm — `target_criteria` (descriptive) → actual pandas code:**

The config expresses rules in business language. The architect resolves them to actual column names using `read_data_schema` output. For every rule in `TRANSFORMATION_LOGIC`:

1. Extract the keywords embedded in `target_criteria` (e.g. `'price'`, `'quantity'`, `'order_id'`).
2. Find the matching column(s) from `read_data_schema` whose names contain any keyword (case-insensitive substring match).
3. Generate pandas code using the **actual discovered column name** and the `on_failure_action` pattern.
4. A descriptive `target_criteria` is never a reason to skip a rule — if the keyword matches a column, the rule applies.

| `on_failure_action` | Pandas pattern |
|---|---|
| `DROP_RECORD` | `chunk = chunk[condition]` |
| `EXCLUDE_AND_LOG` | `_mask = ~condition`; `logging.warning(f"Excluded {_mask.sum()} rows: <reason>.")`; `chunk = chunk[condition]` |
| `DEFAULT_VALUE` | `chunk[col] = chunk[col].where(condition, other=default)` |
| `FLAG_AS_SUSPICIOUS` | accumulate with `\|`: `chunk['is_suspicious'] = flag_rule1 \| flag_rule2` |

**Worked example** — EU Sales pipeline (6 rules → 5 matched columns):
```python
# monetary_integrity: target_criteria 'price' → unit_price column → DROP_RECORD, logic > 0.0
chunk = chunk[chunk['unit_price'] > 0.0]

# temporal_validity: target_criteria 'date'/'timestamp' → order_date → EXCLUDE_AND_LOG
_future = chunk['order_date'] > pd.Timestamp.now()
if _future.any():
    logging.warning(f"Excluded {_future.sum()} future-dated rows (temporal_validity).")
chunk = chunk[~_future]

# completeness_enforcement: target_criteria 'identifier'/'order_id' → order_id → DROP_RECORD
chunk = chunk.dropna(subset=['order_id'])

# currency_standardization: target_criteria 'currency' → currency column → DEFAULT_VALUE 'EUR'
chunk['currency'] = chunk['currency'].where(chunk['currency'].isin(['EUR', 'GBP']), other='EUR')

# volume_sanity_check + quantity_validity: both target 'quantity' → FLAG_AS_SUSPICIOUS, combine with |
chunk['is_suspicious'] = (chunk['quantity'] >= 1000) | (chunk['quantity'] <= 0)
```
Column names (`unit_price`, `order_date`, `order_id`, `currency`, `quantity`) come from `read_data_schema` — never invented or hardcoded from the `target_criteria` description.

### Type Casting
- Cast `float64` → `Int64` for quantity/count columns before every `to_parquet()` call — pandas defaults NULLable integers to float64, causing Trino to read `double` instead of `BIGINT`.
- This step is **MANDATORY** whenever the schema contains integer/quantity/count columns. It must appear as step 3c inside the chunk loop, before `to_parquet()`:
```python
# 3c. Type casting — cast float64 → Int64 for integer/count/quantity columns
int_cols = [c for c in chunk.select_dtypes(include='float64').columns
            if any(kw in c.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
for col in int_cols:
    chunk[col] = chunk[col].astype('Int64')
```
- Omitting this step causes a type mismatch: Trino reads the column as `double` instead of `INTEGER`/`BIGINT`, silently breaking downstream aggregations.

### Cloud SDK
- The cloud storage SDK (`boto3` / `gcs` / `BlobServiceClient`) MUST be used inside `if _CLOUD == "..."` guards — never called unconditionally after a conditional import.

---

## MANDATORY SCRIPT STRUCTURE
Every pipeline script MUST follow this exact skeleton. This is the authoritative execution order — do not reorder steps:

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
    logging.info("Pipeline starting: <pipeline_name>")  # ← MUST be the very first line

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
    # NEVER use os.getenv() directly for DB credentials — it bypasses SSM.
    # Always use the canonical key names (db_host, db_port, db_user, db_password, db_name)
    # with db_type set to the actual engine ("postgres" or "mysql").
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
    # CRITICAL: create_engine AND the loop MUST be in the SAME try block.
    total_rows = 0
    query = "SELECT * FROM <source_table>"  # replace with actual table from context

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):

            # 3a. Date conversion — ALWAYS before any date comparison
            chunk['<date_col>'] = pd.to_datetime(chunk['<date_col>'])

            # 3b. Business rules — translate ALL quality_standards from pipeline config.
            #     NEVER use placeholder values like `is_suspicious = False`.
            #     Every rule MUST be real executable pandas code:
            #
            #   DROP_RECORD:      chunk = chunk[condition]
            #   EXCLUDE_AND_LOG:  excluded = chunk[~condition]
            #                     logging.warning(f"Excluded {len(excluded)} rows: <reason>")
            #                     chunk = chunk[condition]
            #   DEFAULT_VALUE:    chunk[col] = chunk[col].where(condition, other=default)
            #   FLAG_AS_SUSPICIOUS: chunk['is_suspicious'] = ~condition
            #                       # Do NOT filter after flagging — keep all rows

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

    logging.info(f"Pipeline completed. Total rows processed: {total_rows}")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    catalog, schema, table = "<catalog>", "<schema>", "<table>"  # from CATALOG_AND_MONITORING.trino_metadata
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    cursor.execute(f"CALL {catalog}.system.sync_partition_metadata('{schema}', '{table}', 'ADD')")
    cursor.fetchall()
    logging.info(f"Trino partition run_date={run_date} registered.")

    # ── 5. METRICS EMISSION ───────────────────────────────────────────────────
    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id     = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "unknown")

    registry = CollectorRegistry()
    Gauge('pipeline_rows_processed_total', 'Total rows processed',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(total_rows)
    Gauge('pipeline_last_success_timestamp', 'Unix timestamp of last successful run',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(time.time())

    push_to_gateway(pushgateway_url, job=project_id, registry=registry)
    logging.info(f"Metrics pushed to Pushgateway: rows={total_rows}, cloud={cloud_provider}")


if __name__ == "__main__":
    run()
```

---

## Storage URI by Cloud
| Cloud | Protocol | Example |
|---|---|---|
| AWS | `s3://` | `s3://eu-sales-insights-data/processed/` |
| GCP | `gs://` | `gs://global-marketing-insights-data/processed/` |
| Azure | `abfss://` | `abfss://container@account.dfs.core.windows.net/processed/` |

Hidden runtime dependencies (never imported directly):
- `s3fs` for `s3://`, `gcsfs` for `gs://`, `adlfs` for `abfss://`
- `pyarrow` for `to_parquet()`
- `psycopg2-binary` for PostgreSQL via SQLAlchemy

---

## Requirements Standard

**File location:** `requirements.txt` at the **repository root** — never inside `scripts/` or any subdirectory.

```
pandas
sqlalchemy
pyarrow
trino
prometheus-client
# AWS:   boto3, s3fs, psycopg2-binary
# GCP:   google-cloud-storage, gcsfs, pymysql
# Azure: azure-storage-blob, adlfs, pyodbc
```
`trino` is always required. Include only the packages for the active cloud provider.

The filesystem driver (`s3fs` / `gcsfs` / `adlfs`) is **mandatory** — `to_parquet()` cannot write to cloud storage URIs without it.
