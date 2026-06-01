# STANDARD: DATABRICKS PYSPARK + DELTA PIPELINES

For pipelines whose `target_infra_config.provider` is **databricks**. This REPLACES the
pandas/parquet/Trino/Prometheus model — do NOT use `to_parquet`, `cloud_get()`, `create_engine`,
`push_to_gateway`, or any Pushgateway/Trino/Grafana code here.

## Platform model (what is different)
| Pandas/K8s pipeline | Databricks pipeline |
|---|---|
| pandas + `to_parquet` | Spark DataFrame + `write.format("delta")` |
| `cloud_get()` (SSM/env) | `dbutils.secrets.get(scope, key)` |
| Hive partition path `run_date=YYYY-MM-DD/` | Delta table `partitionBy("run_date")` |
| Trino `sync_partition_metadata` | Unity Catalog (automatic) |
| Prometheus gauges → Pushgateway | **Delta audit table** (one row per run) |

## Credentials — Databricks secret scope ONLY
The source DB password is read from a Databricks secret scope (provisioned by the pipeline
Terraform). NEVER use `cloud_get()`, and NEVER use `os.getenv()` with `POSTGRES_DB_*` / `MYSQL_DB_*`
names. Host/user/db arrive as job parameters; the password comes from the secret scope.

## MANDATORY SCRIPT STRUCTURE
```python
import argparse
import datetime
import logging
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

logging.basicConfig(level=logging.INFO)


def run():
    logging.info("Databricks pipeline starting: <pipeline_id>")
    spark = SparkSession.builder.getOrCreate()

    # ── 1. PARAMETERS (passed by the databricks_job task) ─────────────────────
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--secret-scope", required=True)
    args, _ = parser.parse_known_args()

    catalog, schema = args.catalog, args.schema
    table = f"{catalog}.{schema}.<table_name>"
    audit_table = f"{catalog}.{schema}.<table_name>_audit"
    run_date = datetime.date.today().isoformat()
    start_time = time.time()

    # ── 2. CREDENTIALS via Databricks secret scope (NOT cloud_get) ────────────
    db_host = dbutils.secrets.get(args.secret_scope, "db_host")          # noqa: F821 (dbutils is injected by Databricks)
    db_name = dbutils.secrets.get(args.secret_scope, "db_name")          # noqa: F821
    db_user = dbutils.secrets.get(args.secret_scope, "db_user")          # noqa: F821
    db_password = dbutils.secrets.get(args.secret_scope, "db_password")  # noqa: F821
    jdbc_url = f"jdbc:postgresql://{db_host}:5432/{db_name}"

    # ── 3. IDEMPOTENCY — skip if this run_date already landed ─────────────────
    if spark.catalog.tableExists(table):
        already = spark.table(table).where(F.col("run_date") == run_date).limit(1).count()
        if already:
            logging.info(f"run_date={run_date} already present in {table}. Skipping.")
            return

    # ── 4. EXTRACT (Spark JDBC) ───────────────────────────────────────────────
    df = (
        spark.read.format("jdbc")
        .option("url", jdbc_url)
        .option("dbtable", "<source_table>")
        .option("user", db_user)
        .option("password", db_password)
        .option("driver", "org.postgresql.Driver")
        .load()
    )

    # ── 5. BUSINESS RULES (translate ALL quality_standards) ───────────────────
    # Track per-rule rejections with a FRESH count before each row-removing rule.
    rejected_by_reason = {}

    # DROP_RECORD example (monetary_integrity):
    _before = df.count()
    df = df.filter(F.col("unit_price") > 0.0)
    rejected_by_reason["monetary_integrity"] = _before - df.count()

    # EXCLUDE_AND_LOG example (temporal_validity):
    _before = df.count()
    df = df.filter(F.col("order_date") <= F.current_timestamp())
    rejected_by_reason["temporal_validity"] = _before - df.count()

    # DEFAULT_VALUE → withColumn(when(...)); FLAG_AS_SUSPICIOUS → withColumn("is_suspicious", ~cond)
    # (these do NOT remove rows → no rejected_by_reason entry)

    rows_rejected = sum(rejected_by_reason.values())

    # ── 6. WRITE to Delta (Unity Catalog, partitioned by run_date) ────────────
    out = df.withColumn("run_date", F.lit(run_date))
    rows_processed = out.count()
    (
        out.write.format("delta")
        .mode("overwrite")
        .option("replaceWhere", f"run_date = '{run_date}'")  # idempotent per-partition overwrite
        .partitionBy("run_date")
        .saveAsTable(table)
    )

    duration_seconds = time.time() - start_time
    logging.info(f"Wrote {rows_processed} rows to {table} (rejected={rows_rejected}).")

    # ── 7. AUDIT TABLE (MANDATORY — Databricks-native metrics) ────────────────
    # One row per run: the Delta equivalent of the Prometheus gauges. Every pipeline
    # records its own health, consistently across all clouds.
    audit_row = [(
        datetime.datetime.utcnow(),               # run_timestamp
        run_date,                                  # run_date
        int(rows_processed),                       # rows_processed
        int(rows_rejected),                        # rows_rejected
        float(duration_seconds),                   # duration_seconds
        {k: int(v) for k, v in rejected_by_reason.items()},  # rejected_by_reason map
    )]
    audit_cols = ["run_timestamp", "run_date", "rows_processed",
                  "rows_rejected", "duration_seconds", "rejected_by_reason"]
    spark.createDataFrame(audit_row, audit_cols) \
        .write.format("delta").mode("append").saveAsTable(audit_table)
    logging.info(
        f"Audit row written: rows_processed={rows_processed}, rows_rejected={rows_rejected}, "
        f"duration={duration_seconds:.1f}s, by_reason={rejected_by_reason}"
    )


if __name__ == "__main__":
    run()
```

## Unity Catalog DDL (`sql/setup_unity_catalog.sql`)
Delta + Unity Catalog syntax — NOT Trino-Hive. Create BOTH the data table and the `_audit` table.
```sql
CREATE CATALOG IF NOT EXISTS multi_cloud_agent_workspace;
CREATE SCHEMA  IF NOT EXISTS multi_cloud_agent_workspace.raw;

CREATE TABLE IF NOT EXISTS multi_cloud_agent_workspace.raw.<table_name> (
    -- columns from read_data_schema ...
    run_date STRING
) USING DELTA
PARTITIONED BY (run_date);

CREATE TABLE IF NOT EXISTS multi_cloud_agent_workspace.raw.<table_name>_audit (
    run_timestamp TIMESTAMP,
    run_date STRING,
    rows_processed BIGINT,
    rows_rejected BIGINT,
    duration_seconds DOUBLE,
    rejected_by_reason MAP<STRING, BIGINT>
) USING DELTA;
```
- Use `USING DELTA` — never `external_location` / `PARTITIONED_BY = ARRAY[...]` / `FORMAT = 'PARQUET'` (that is Trino-Hive syntax and does not apply to Unity Catalog).
- 3-part names `catalog.schema.table` everywhere.

## Hard rules
- No `to_parquet`, no `cloud_get()`, no `os.getenv("POSTGRES_DB_*"/"MYSQL_DB_*")`, no `create_engine`,
  no `push_to_gateway`, no Trino/Grafana code.
- The audit-table write is **MANDATORY** — a run that writes data but no audit row is non-compliant.
