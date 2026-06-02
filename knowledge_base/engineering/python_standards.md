# STANDARD: PYTHON DATA PIPELINES
All Python scripts generated for data engineering must follow these standards exactly.
After writing any `.py` file, you MUST call `validate_generated_code` on it — if it returns errors, fix them before proceeding.

---

## CRITICAL RULES — read these BEFORE writing any code

These violations cause immediate runtime failure. No exceptions.

### Code syntax — single braces only
- The generated file is a plain Python script — it is NOT a template and is NOT passed through `.format()`. Use **single** braces everywhere: `f"{var}"` for f-string placeholders and `{}` for empty dicts.
- **NEVER double braces.** `storage_options={{}}` is a set containing a dict → `TypeError: unhashable type: dict`. `f"part_{{i}}.parquet"` produces the literal text `part_{i}.parquet` (no substitution) and trips ruff `F541`.
```python
# ❌ WRONG — double braces:
chunk.to_parquet(f"{partition_uri}part_{{i}}.parquet", storage_options={{}})
# ✅ CORRECT — single braces:
chunk.to_parquet(f"{partition_uri}part_{i}.parquet", storage_options={})
```

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
    host = cloud_get("gcp", "db_host", db_type="mysql")
    connection_string = f"mysql+pymysql://..."   # global_marketing source = GCP MySQL
elif _CLOUD == "azure":
    host = cloud_get("azure", "db_host", db_type="postgres")
    connection_string = f"postgresql+psycopg2://..."   # us_crm source = Azure Postgres
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

**Worked example** — EU Sales pipeline (6 rules → 5 matched columns). **CRITICAL: each row-removing rule MUST take a FRESH `_before = len(chunk)` immediately before ITS OWN filter.** A single shared `_before` captured once at the top is the most common bug — it makes every rule report the *cumulative* drop so far (`_before - len(chunk)`), so the deltas double-count and `sum(by_reason)` explodes far above the real total. The fresh-per-rule reading is what guarantees the invariant `sum(rejected_by_reason.values()) == rejected_rows`.
```python
# monetary_integrity: target_criteria 'price' → unit_price column → DROP_RECORD, logic > 0.0
_before = len(chunk)
chunk = chunk[chunk['unit_price'] > 0.0]
rejected_by_reason['monetary_integrity'] = \
    rejected_by_reason.get('monetary_integrity', 0) + (_before - len(chunk))

# temporal_validity: target_criteria 'date'/'timestamp' → order_date → EXCLUDE_AND_LOG
_before = len(chunk)                       # FRESH reading — NOT the value from the rule above
_future = chunk['order_date'] > pd.Timestamp.now()
if _future.any():
    logging.warning(f"Excluded {_future.sum()} future-dated rows (temporal_validity).")
chunk = chunk[~_future]
rejected_by_reason['temporal_validity'] = \
    rejected_by_reason.get('temporal_validity', 0) + (_before - len(chunk))

# completeness_enforcement: target_criteria 'identifier'/'order_id' → order_id → DROP_RECORD
_before = len(chunk)                       # FRESH reading
chunk = chunk.dropna(subset=['order_id'])
rejected_by_reason['completeness_enforcement'] = \
    rejected_by_reason.get('completeness_enforcement', 0) + (_before - len(chunk))

# currency_standardization: target_criteria 'currency' → currency column → DEFAULT_VALUE 'EUR'
#   (DEFAULT_VALUE does not remove rows → no rejected_by_reason entry)
chunk['currency'] = chunk['currency'].where(chunk['currency'].isin(['EUR', 'GBP']), other='EUR')

# volume_sanity_check + quantity_validity: both target 'quantity' → FLAG_AS_SUSPICIOUS, combine with |
#   (FLAG_AS_SUSPICIOUS does not remove rows → no rejected_by_reason entry)
chunk['is_suspicious'] = (chunk['quantity'] >= 1000) | (chunk['quantity'] <= 0)
```
After the chunk loop, DERIVE the scalar total from the per-reason dict — do NOT maintain a
separate `rejected_rows +=` counter inside the loop (the LLM reliably updates it after only
one rule, so the scalar disagrees with the per-reason sum):
```python
rejected_rows = sum(rejected_by_reason.values())   # single source of truth
```
This makes `rejected_rows == sum(rejected_by_reason.values())` true by construction, so the
Rejection Rate panel (uses the scalar) and the Rejections-by-Reason panel (uses the dict)
can never disagree.
Column names (`unit_price`, `order_date`, `order_id`, `currency`, `quantity`) come from `read_data_schema` — never invented or hardcoded from the `target_criteria` description. The `reason` keys (`monetary_integrity`, `temporal_validity`, `completeness_enforcement`) are the rule names straight from `quality_standards` — never hardcoded literals invented by the architect.

### PII anonymization (ONLY when `pii_sensitive: true`)
When the pipeline config sets `pii_sensitive: true`, anonymize PII columns as an **unconditional transform** applied to every row inside the chunk loop, BEFORE the business rules. It is NOT a `quality_standards` rule and removes no rows. Hashing needs `import hashlib` at the top of the file (add it ONLY when a column is hashed — an unused import trips ruff `F401`).
```python
import hashlib  # top-of-file import, only when a column is hashed
# ...
# Hash a name column (irreversible) — resolve <name_col> from read_data_schema:
chunk['<name_col>'] = chunk['<name_col>'].apply(
    lambda v: hashlib.sha256(str(v).encode()).hexdigest())
# Mask an email column → keeps first char + domain (b***@example.org):
chunk['<email_col>'] = chunk['<email_col>'].str.replace(r'(?<=.).*?(?=@)', '***', regex=True)
```
Omit this block entirely when `pii_sensitive` is absent or false.

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

### Metrics
- Emit **exactly five** Gauges to Pushgateway:
  - **Four scalar metrics** with `['project_id', 'cloud_provider']` labels: `pipeline_rows_processed_total` (volume), `pipeline_last_success_timestamp` (freshness), `pipeline_rows_rejected_total` (data quality — total), `pipeline_duration_seconds` (performance).
  - **One labeled metric** `pipeline_rows_rejected_by_reason` with `['project_id', 'cloud_provider', 'reason']` labels — emits **one series per business rule**, where `reason` is the rule name from `quality_standards`. This is the per-rule breakdown of the total `pipeline_rows_rejected_total`.
  The Grafana dashboard renders one panel per metric — omitting any leaves a "No data" panel.
- **Per-rule attribution (pipeline-agnostic — never hardcode reasons):** maintain a `rejected_by_reason` dict (`rule_name → cumulative dropped rows`). Each `DROP_RECORD` / `EXCLUDE_AND_LOG` rule wraps its filter with its OWN FRESH `_before = len(chunk)` (taken immediately before that rule's filter) and adds the delta under its own rule name. `DEFAULT_VALUE` / `FLAG_AS_SUSPICIOUS` do not remove rows, so they get no entry.
- `rejected_rows` (scalar total) is **DERIVED** after the loop as `sum(rejected_by_reason.values())` — never a separate in-loop `+=` counter (that reliably drifts out of sync). `duration_seconds` is `time.time() - start_time` captured after the extract loop. See the skeleton.

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
        # global_marketing's source is GCP Cloud SQL for MySQL → db_type="mysql" so the
        # credential lookup resolves MYSQL_DB_* (not POSTGRES_DB_*). Driver follows the
        # source engine, not the cloud — see the AWS/Azure notes above.
        host = cloud_get("gcp", "db_host",     db_type="mysql")
        port = cloud_get("gcp", "db_port",     db_type="mysql") or "3306"
        user = cloud_get("gcp", "db_user",     db_type="mysql")
        pw   = cloud_get("gcp", "db_password", db_type="mysql")
        db   = cloud_get("gcp", "db_name",     db_type="mysql")
        connection_string = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"
    elif _CLOUD == "azure":
        # The DB DRIVER is chosen by the SOURCE engine (DATA_SOURCE.type), NOT the cloud.
        # us_crm's source is Azure Database for PostgreSQL → postgresql+psycopg2 (port 5432).
        # Use mssql+pyodbc (port 1433) ONLY if the source is Azure SQL / MSSQL.
        host = cloud_get("azure", "db_host",     db_type="postgres")
        port = cloud_get("azure", "db_port",     db_type="postgres") or "5432"
        user = cloud_get("azure", "db_user",     db_type="postgres")
        pw   = cloud_get("azure", "db_password", db_type="postgres")
        db   = cloud_get("azure", "db_name",     db_type="postgres")
        connection_string = f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"

    # ── 3. EXTRACTION + TRANSFORMATION + WRITE (one try block) ───────────────
    # CRITICAL: create_engine AND the loop MUST be in the SAME try block.
    start_time = time.time()   # for pipeline_duration_seconds metric
    total_rows = 0
    rejected_by_reason = {}    # rule_name → cumulative dropped rows (one entry per row-removing rule)
    # NOTE: rejected_rows is NOT maintained here — it is derived after the loop as
    # sum(rejected_by_reason.values()) so the scalar and per-reason can never disagree.
    query = "SELECT * FROM <source_table>"  # replace with actual table from context

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):

            # 3a. Date conversion — ONLY when the discovered schema actually HAS a date/
            #     timestamp column that a business rule compares against. If the table has no
            #     date column (e.g. a CRM customers table: id/name/email/phone), OMIT this
            #     step entirely. NEVER force pd.to_datetime on a non-date column (e.g. a name)
            #     — it raises ValueError / yields NaT and crashes the run.
            chunk['<date_col>'] = pd.to_datetime(chunk['<date_col>'])   # delete if no date column exists

            # 3b. Business rules — translate ALL quality_standards from pipeline config.
            #     NEVER use placeholder values like `is_suspicious = False`.
            #     Each row-removing rule takes its OWN FRESH `_before = len(chunk)` immediately
            #     before ITS filter and accumulates the delta under its own quality_standards
            #     rule name (the `reason` keys come from config — NEVER hardcoded literals).
            #     A single shared `_before` captured once at the top double-counts — see the
            #     Worked Example above.
            #
            #   DROP_RECORD:      _before = len(chunk)
            #                     chunk = chunk[condition]
            #                     rejected_by_reason['<rule_name>'] = \
            #                         rejected_by_reason.get('<rule_name>', 0) + (_before - len(chunk))
            #   EXCLUDE_AND_LOG:  _before = len(chunk)
            #                     _mask = ~condition
            #                     logging.warning(f"Excluded {_mask.sum()} rows: <reason>")
            #                     chunk = chunk[condition]
            #                     rejected_by_reason['<rule_name>'] = \
            #                         rejected_by_reason.get('<rule_name>', 0) + (_before - len(chunk))
            #   DEFAULT_VALUE:    chunk[col] = chunk[col].where(condition, other=default)
            #                     # does NOT remove rows → no rejected_by_reason entry
            #   FLAG_AS_SUSPICIOUS: chunk['is_suspicious'] = ~condition
            #                       # Do NOT filter after flagging — keep all rows
            #                       # does NOT remove rows → no rejected_by_reason entry
            #
            # Do NOT keep an in-loop `rejected_rows += ...` counter — the scalar total is
            # DERIVED after the loop as sum(rejected_by_reason.values()) (see below), so the
            # two can never drift out of sync.

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

    # Scalar total DERIVED from the per-reason dict — single source of truth, so the
    # Rejection Rate panel (which uses rejected_rows) and the Rejections-by-Reason panel
    # (which uses the dict) can never disagree.
    rejected_rows = sum(rejected_by_reason.values())
    duration_seconds = time.time() - start_time   # for pipeline_duration_seconds metric
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

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

    # Emit ALL FIVE metrics — the Grafana dashboard renders one panel per metric
    # (volume, freshness, data quality, performance, per-reason breakdown).
    # Omitting any leaves a panel with "No data".
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

    # Per-rule breakdown — one series per business rule that removed rows.
    # `reason` is the quality_standards rule name (never hardcoded). A pipeline with no
    # DROP_RECORD / EXCLUDE_AND_LOG rules emits zero series here (panel shows "No data").
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

The file is the shared block PLUS the active cloud's block. Copy the matching cloud block **verbatim — omit NOTHING**. Each cloud needs THREE distinct things and dropping any one fails at runtime:
- object-storage SDK · `to_parquet()` filesystem driver · DB driver

**Shared (always):**
```
pandas
sqlalchemy
pyarrow
trino
prometheus-client
```
**AWS — append all three:**
```
boto3
s3fs
psycopg2-binary
```
**GCP — append all three:**
```
google-cloud-storage
gcsfs
pymysql
```
**Azure — append all three:**
```
azure-storage-blob
adlfs
psycopg2-binary
```
(Use `pyodbc` instead of `psycopg2-binary` ONLY if the source is Azure SQL / MSSQL — not Postgres.)

The filesystem driver (`s3fs` / `gcsfs` / `adlfs`) and the DB driver (`psycopg2-binary` / `pymysql`) are BOTH mandatory: without the filesystem driver `to_parquet()` cannot write to a cloud URI; without the DB driver SQLAlchemy cannot connect.
