---
id: python-standards
applies_to: aws, azure, gcp (object-storage)
primary_consumer: architect-agent   # retrieved via Pinecone (query_vector_store); medic may also retrieve it
enforced_by: validate_generated_code (safety net) + agent prompts
last_reviewed: 2026-06-15
---

# STANDARD: PYTHON DATA PIPELINES
All Python scripts generated for data engineering must follow these standards exactly.
After writing any `.py` file, automatic validation runs (`validate_generated_code`, in Python — it is NOT an LLM tool call). If the result reports errors, fix them and rewrite the file before proceeding.

---

## CRITICAL RULES — read these BEFORE writing any code

These violations cause immediate runtime failure. No exceptions.

### Code syntax — single braces only
- The generated file is a plain Python script — it is NOT a template and is NOT passed through `.format()`. Use a SINGLE pair of braces for an f-string placeholder: `f"{var}"`. For the empty `storage_options` dict, use **`dict()`** — it has no braces, so it can never be accidentally double-braced.
- **Never double the braces** — a second pair cancels the substitution (emits the literal text, trips ruff `F541`). `storage_options=dict()` sidesteps the `{{}}` trap (which builds a set-of-dict → `TypeError: unhashable type: dict`). The one brace site is the part-file f-string — exactly one pair:
```python
# ✅ CORRECT — dict() for the empty options, one pair of braces in the f-string:
chunk.to_parquet(f"{partition_uri}part_{i}.parquet", storage_options=dict())
```

### Credentials
- `cloud_get()` is MANDATORY for all DB credentials. `os.getenv()` is FORBIDDEN for host/user/password/db — it bypasses SSM and returns None in production.
- Import: `from utils.cloud_config import cloud_get` — place after standard library imports, before cloud SDK block.
- Connection strings MUST use double-quoted outer f-strings to avoid `SyntaxError: f-string: unmatched '('`.
- Every `cloud_get()` call and the `connection_string` assignment MUST be inside a cloud-specific guard (`if _CLOUD == "aws":` / `elif _CLOUD == "gcp":` / `elif _CLOUD == "azure":`). An unguarded `cloud_get("aws", ...)` hardcodes AWS credentials into a supposedly cloud-agnostic script — it will fail silently when `CLOUD_PROVIDER=gcp` or `CLOUD_PROVIDER=azure` because the wrong credential keys are resolved.
- **Emit the FULL three-cloud skeleton verbatim** — keep ALL THREE branches (`if _CLOUD == "aws":` / `elif _CLOUD == "gcp":` / `elif _CLOUD == "azure":`), each with a REAL body, in the cloud-SDK import, idempotency, AND credentials blocks. Only the active `CLOUD_PROVIDER` branch runs (imports are conditional), so all three are always valid — the structure the validated AWS/Azure pipelines use. **Do NOT collapse to one branch or drop the others** (collapsing flattens the `if _CLOUD ==` guard → CLOUD-GUARD failure, or drops the SDK import → F821). **NEVER** leave a branch empty/comment-only (e.g. `# Add AWS logic here`) — a comment-only body is a `SyntaxError` that `patch_project_file` rejects, dead-looping the self-heal.
```python
# ❌ WRONG — unguarded, breaks on GCP/Azure:
host = cloud_get("aws", "db_host", db_type="postgres")
connection_string = f"postgresql+psycopg2://..."

# ✅ CORRECT — the FULL skeleton, every branch a real body (only the active cloud runs):
if _CLOUD == "aws":
    host = cloud_get("aws", "db_host", db_type="postgres")
    connection_string = f"postgresql+psycopg2://..."
elif _CLOUD == "gcp":
    host = cloud_get("gcp", "db_host", db_type="mysql")
    connection_string = f"mysql+pymysql://..."   # global_marketing source = GCP MySQL
elif _CLOUD == "azure":
    host = cloud_get("azure", "db_host", db_type="postgres")
    connection_string = f"postgresql+psycopg2://..."   # us_crm source = Azure Postgres

# ❌ FATAL — an empty / comment-only branch body is a SyntaxError that dead-loops the heal.
# Give EVERY branch the real implementation above — never a placeholder comment:
elif _CLOUD == "gcp":
    # Add GCP credentials logic here     ← never do this
```

### Storage
- `storage_options=dict()` is MANDATORY in every `to_parquet()` call — omitting it causes `TypeError` on cloud storage writes (s3://, gs://, abfss://). Use `dict()`, not `{}`, to avoid the `{{}}` double-brace trap.
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

**Removal rules BOTH count:** `EXCLUDE_AND_LOG` removes rows exactly like `DROP_RECORD` (only ADDS a `logging.warning`) — it MUST also increment `rejected_by_reason` under its rule name (fresh `_before` → delta). Omitting it → rows vanish from storage but never appear in the rejection metrics (silent under-count).

**"At least one of N columns non-NULL" → `dropna(how='all')`:** *"email OR phone must be present"* drops a row only when ALL listed columns are null: `chunk.dropna(subset=['email','phone'], how='all')`. The default `how='any'` over-rejects (drops when ANY is null) — WRONG for OR-semantics. Single-column completeness is unaffected (any == all).

**Numeric columns — coerce with `pd.to_numeric` (NEVER `.astype(float)`), in a SEPARATE statement before comparing.** A numerically compared/clamped column may carry dirty values. Coerce, **assign back FIRST**, THEN compare/clamp on the now-numeric column. `.astype(float)` raises `ValueError` on the first bad value (validator catches it). Chaining the comparison onto the coerce reads the ORIGINAL `str` column (not yet assigned) → `TypeError: Invalid comparison between dtype=str and int` — a RUNTIME-only crash the validator can't see.
```python
# ❌ .where reads the un-coerced (string) column → TypeError at runtime
chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce').where(chunk['ad_spend'] >= 0, other=0)
# ✅ coerce + assign back FIRST, THEN clamp
chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce')
chunk['ad_spend'] = chunk['ad_spend'].fillna(0).clip(lower=0)   # non-numeric→NaN→0, negative→0
```
(`.astype('Int64')` for the final integer cast is separate — see Storage.)

**Temporal/date comparison columns — coerce with `pd.to_datetime` FIRST:** a column compared to a `Timestamp` may arrive as a STRING. `str > Timestamp` raises `TypeError: Invalid comparison between dtype=str and Timestamp`. Coerce **before** comparing: `chunk[col] = pd.to_datetime(chunk[col], errors='coerce')` — dirty → `NaT` (dropped). Like `pd.to_numeric`, ALWAYS coerce — never rely on a source DATE type.

**Worked example** — EU Sales (6 rules → 5 columns). **Each row-removing rule MUST take a FRESH `_before = len(chunk)` immediately before ITS OWN filter** — a single shared `_before` makes every rule report the *cumulative* drop, so the deltas double-count and `sum(by_reason)` explodes. Fresh-per-rule guarantees `sum(rejected_by_reason.values()) == rejected_rows`.
```python
# monetary_integrity: target_criteria 'price' → unit_price column → DROP_RECORD, logic > 0.0
chunk['unit_price'] = pd.to_numeric(chunk['unit_price'], errors='coerce')  # dirty/non-numeric → NaN
_before = len(chunk)
chunk = chunk[chunk['unit_price'] > 0.0]    # NaN (coerced dirty) and <=0 dropped → counted as rejected
rejected_by_reason['monetary_integrity'] = \
    rejected_by_reason.get('monetary_integrity', 0) + (_before - len(chunk))

# temporal_validity: target_criteria 'date'/'timestamp' → order_date → EXCLUDE_AND_LOG
chunk['order_date'] = pd.to_datetime(chunk['order_date'], errors='coerce')  # str/VARCHAR/dirty → NaT
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
After the chunk loop, DERIVE the scalar total from the per-reason dict — do NOT keep a separate
`rejected_rows +=` counter inside the loop (the LLM updates it after only one rule → it disagrees
with the per-reason sum):
```python
rejected_rows = sum(rejected_by_reason.values())   # single source of truth
```
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
# Mask a phone column → keeps only the last 4 digits (***-***-6789):
chunk['<phone_col>'] = chunk['<phone_col>'].astype(str).str.replace(r'\d(?=\d{4})', '*', regex=True)
```
**Copy the mask patterns above VERBATIM — never improvise your own.** For phone numbers use the exact `r'\d(?=\d{4})'` pattern shown (a simple look-ahead that stars every digit followed by 4+ more). Do NOT invent a `\b`-based look-behind like `r'(?<=\b)\b(\b\d{3})...'` — it reliably mangles into an unterminated string or invalid syntax and breaks the whole file.
**Every `.str.replace()` that takes a regex pattern MUST pass `regex=True`.** In pandas 2.x the default is `regex=False`, so the pattern is a literal — the mask matches nothing and **silently no-ops**, leaving the PII column exposed with no error. Applies to any masked column (phone, SSN, …), not just email.
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
- The cloud storage SDK is imported in the FULL conditional import block (all three `if _CLOUD == ...` branches, kept verbatim) and CALLED (`boto3.client(...)` / `storage.Client()` / `BlobServiceClient(...)`) inside the matching branch of the idempotency block — every branch present, the import never dropped, the call never left unguarded.

### Metrics
- Emit **exactly five** Gauges to Pushgateway:
  - **Four scalar metrics** with `['project_id', 'cloud_provider']` labels: `pipeline_rows_processed_total` (volume), `pipeline_last_success_timestamp` (freshness), `pipeline_rows_rejected_total` (data quality — total), `pipeline_duration_seconds` (performance).
  - **One labeled metric** `pipeline_rows_rejected_by_reason` with `['project_id', 'cloud_provider', 'reason']` labels — emits **one series per business rule**, where `reason` is the rule name from `quality_standards`. This is the per-rule breakdown of the total `pipeline_rows_rejected_total`.
  The Grafana dashboard renders one panel per metric — omitting any leaves a "No data" panel.
- **Per-rule attribution (never hardcode reasons):** maintain a `rejected_by_reason` dict (`rule_name → dropped rows`); each `DROP_RECORD`/`EXCLUDE_AND_LOG` rule adds its delta under its own rule name (fresh `_before` per rule — see Business Rules). `DEFAULT_VALUE`/`FLAG_AS_SUSPICIOUS` remove no rows → no entry.
- `rejected_rows` is **DERIVED** after the loop as `sum(rejected_by_reason.values())` — never an in-loop `+=` (it drifts out of sync). `duration_seconds = time.time() - start_time` after the extract loop. See the skeleton.

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

# Cloud-specific storage SDK — emit this FULL if/elif block VERBATIM (all three clouds).
# The imports are conditional, so on a single-cloud image only the matching branch runs
# and only that SDK is imported — the unused ones never execute, so it is safe that they
# are not installed. NEVER drop or collapse this block: a missing import is the F821
# 'Undefined name' failure (e.g. `storage.Client()` with no import).
_CLOUD = os.getenv("CLOUD_PROVIDER", "aws")
if _CLOUD == "aws":
    import boto3
elif _CLOUD == "gcp":
    from google.cloud import storage   # used as storage.Client()
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
        client = storage.Client()
        blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
        if blobs:
            logging.info("Destination already populated. Skipping.")
            return
    elif _CLOUD == "azure":
        # The abfss netloc is "<container>@<account>.dfs.core.windows.net" — the real
        # container name is ONLY the segment BEFORE '@'. Passing the full netloc to
        # get_container_client() sends '@' and '.' in the container name → Azure rejects
        # it with HTTP 400 InvalidResourceName ("resource name contains invalid characters").
        # (s3:// and gs:// put the bucket directly in netloc, so this split is azure-only.)
        container_name = bucket.split('@')[0]
        client = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
        container = client.get_container_client(container_name)
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

            # 3d. Write — storage_options is MANDATORY, do not omit it. Use dict() (NOT {})
            # so the empty-dict literal has no braces to accidentally double-brace into {{}}.
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

    # Scalar total DERIVED from the per-reason dict — single source of truth, so the
    # Rejection Rate panel (which uses rejected_rows) and the Rejections-by-Reason panel
    # (which uses the dict) can never disagree.
    rejected_rows = sum(rejected_by_reason.values())
    duration_seconds = time.time() - start_time   # for pipeline_duration_seconds metric
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    # Fill the bare schema and table names as STRING LITERALS directly in the CALL below —
    # exactly as the catalog "hive" is a literal. Do NOT assign `schema`/`table` (or `catalog`)
    # variables: the model tends to fill the real value into BOTH the assignment AND the string,
    # leaving the variable unused (ruff F841) and the f-string placeholder-less (ruff F541). Use
    # a PLAIN string (no f-prefix) with the literals inlined.
    # sync_partition_metadata takes EXACTLY THREE args: ('<schema>', '<table>', 'ADD'). The
    # catalog `hive` lives ONLY in the `hive.system.` prefix — NEVER inside the args, in either
    # of these two wrong shapes:
    #   ❌ CALL hive.system.sync_partition_metadata('hive.marketing_global', 'orders', 'ADD')
    #        → Trino looks for a schema named 'hive.marketing_global' → "Table ... not found"
    #   ❌ CALL hive.system.sync_partition_metadata('hive', 'marketing_global', 'orders', 'ADD')
    #        → 4 args: the mode 'ADD' is cast to the boolean case_sensitive param →
    #          "Cannot cast type varchar(3) to boolean"
    #   ✅ CALL hive.system.sync_partition_metadata('marketing_global', 'orders', 'ADD')
    # The schema is the BARE middle segment of `hive.<schema>.<table>`. If the objective shows the
    # target fully-qualified, use only the middle segment — and never add `hive` as its own arg.
    # Retry the partition registration. A freshly-started Trino coordinator (the init container
    # creates this table only seconds before the pipeline runs) may not yet see it in its Glue
    # catalog and raises "Table ... not found" TRANSIENTLY — the table IS in Glue and becomes
    # visible within a few seconds. Retry rather than crash: a crash here aborts BEFORE the
    # metrics emission below, leaving EVERY Grafana panel empty for an otherwise-successful run.
    for _attempt in range(5):
        try:
            cursor.execute("CALL hive.system.sync_partition_metadata('<schema>', '<table>', 'ADD')")
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

> **GENERATION: CODE-OWNED.** `requirements.txt` is rendered deterministically by
> `agents/codegen.py:render_requirements` — the architect never writes it. This
> section is the SPEC for that generator.

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
