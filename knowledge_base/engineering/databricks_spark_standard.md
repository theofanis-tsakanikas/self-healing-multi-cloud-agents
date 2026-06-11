---
id: databricks-spark-standard
applies_to: databricks
primary_consumer: architect-agent   # retrieved via Pinecone (query_vector_store); medic may also retrieve it
enforced_by: validate_generated_code (safety net) + agent prompts
last_reviewed: 2026-06-11
---

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

## Credentials — host/user/db are job parameters, PASSWORD is a secret scope
The non-sensitive connection info (`--db-host`, `--db-name`, `--db-user`) arrives as **job
parameters** (the `databricks_job` passes them; the pipeline Terraform reads the values from
**SSM** via `data "aws_ssm_parameter"`). Only the
**password** is sensitive → read it from the Databricks **secret scope** (provisioned by the
pipeline Terraform) with `dbutils.secrets.get(scope, "db_password")`. NEVER use `cloud_get()`,
and NEVER use `os.getenv()` with `POSTGRES_DB_*` / `MYSQL_DB_*` names. (The Terraform stores
ONLY `db_password` in the scope — the script must NOT `secrets.get` host/name/user, those keys
do not exist; read them from the parsed args.)

## Source JDBC driver — 🔴 the job MUST load it
A Databricks cluster does NOT ship the Postgres/MySQL JDBC driver. The `databricks_job`'s task
MUST attach it as a Maven `library` (e.g. `org.postgresql:postgresql:42.7.3`) — see
`terraform_databricks.md`. Without it the `spark.read.format("jdbc")` fails
`java.lang.ClassNotFoundException: org.postgresql.Driver`.

## MANDATORY SCRIPT STRUCTURE
```python
import argparse
import datetime
import logging
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

logging.basicConfig(level=logging.INFO)
# MANDATORY: silence py4j. basicConfig(level=INFO) sets the ROOT logger to INFO, which makes the
# py4j logger emit one line PER Python↔JVM call ("Received command c on object id p0"). Across the
# thousands of calls a Spark job makes, that log flood becomes the driver bottleneck — a 100-row
# job crawls for 10+ minutes. WARNING keeps our own INFO logs but mutes the py4j chatter.
logging.getLogger("py4j").setLevel(logging.WARNING)
logging.getLogger("py4j.clientserver").setLevel(logging.WARNING)


def run():
    logging.info("Databricks pipeline starting: <pipeline_id>")
    spark = SparkSession.builder.getOrCreate()

    # ── 1. PARAMETERS (passed by the databricks_job task) ─────────────────────
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--secret-scope", required=True)
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-user", required=True)
    args, _ = parser.parse_known_args()

    catalog, schema = args.catalog, args.schema
    # <table_name> is the BARE table name from DELTA_DESTINATION.unity_catalog.table
    # (e.g. "pipe_sales_lakehouse") — NOT the fully-qualified name.
    #   ✅ table = f"{catalog}.{schema}.pipe_sales_lakehouse"
    #   ❌ table = f"{catalog}.{schema}.multi_cloud_agent_workspace.raw.pipe_sales_lakehouse"  (5-part — fails)
    table = f"{catalog}.{schema}.<table_name>"
    audit_table = f"{catalog}.{schema}.<table_name>_audit"
    run_date = datetime.date.today().isoformat()
    start_time = time.time()

    # ── 2. CREDENTIALS — host/name/user from job params; PASSWORD from the secret scope ──
    db_host, db_name, db_user = args.db_host, args.db_name, args.db_user
    db_password = dbutils.secrets.get(args.secret_scope, "db_password")  # noqa: F821 (dbutils injected by Databricks)
    # ?sslmode=require is MANDATORY for RDS Postgres: the instance enforces SSL (rds.force_ssl),
    # and the pgjdbc default (sslmode=prefer) HANGS on the SSL negotiation from the Databricks
    # cluster — the read task never completes (TCP connects, then stalls forever). require = clean
    # encrypted connection, no cert verification. (MySQL source → use "?useSSL=true&requireSSL=true".)
    # connectTimeout/socketTimeout bound the connection so a stall fails fast (a clear error) instead
    # of hanging the run indefinitely.
    jdbc_url = f"jdbc:postgresql://{db_host}:5432/{db_name}?sslmode=require&connectTimeout=15&socketTimeout=120"

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
        .cache()  # the business-rule counts below re-scan df repeatedly — cache so the JDBC read
                  # against Postgres runs ONCE, not once per count()/write (N round-trips → 1).
    )

    # ── 5. BUSINESS RULES (translate ALL quality_standards) ───────────────────
    # Track per-rule rejections with a FRESH count before each row-removing rule.
    rejected_by_reason = {}

    # DROP_RECORD example (monetary_integrity) — cast numeric columns explicitly so a dirty
    # value becomes NULL (dropped by the filter), never a runtime cast error:
    _before = df.count()
    df = df.filter(F.col("unit_price").cast("double") > 0.0)
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
        datetime.datetime.now(datetime.timezone.utc),  # run_timestamp
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
-- <catalog> / <schema> come from DELTA_DESTINATION.unity_catalog — never invented.
CREATE CATALOG IF NOT EXISTS <catalog>;
CREATE SCHEMA  IF NOT EXISTS <catalog>.<schema>;

CREATE TABLE IF NOT EXISTS <catalog>.<schema>.<table_name> (
    -- columns from read_data_schema ...
    run_date STRING
) USING DELTA
PARTITIONED BY (run_date);

CREATE TABLE IF NOT EXISTS <catalog>.<schema>.<table_name>_audit (
    run_timestamp TIMESTAMP,
    run_date STRING,
    rows_processed BIGINT,
    rows_rejected BIGINT,
    duration_seconds DOUBLE,
    rejected_by_reason MAP<STRING, BIGINT>
) USING DELTA;
```
- Use `USING DELTA` — never `external_location` / `PARTITIONED_BY = ARRAY[...]` / `FORMAT = 'PARQUET'` (that is Trino-Hive syntax and does not apply to Unity Catalog).
- 3-part names `catalog.schema.table` — but the Spark script BUILDS that name from the
  job-param `{catalog}.{schema}` + the BARE table (`f"{catalog}.{schema}.{table}"`). Never paste
  an already-qualified `catalog.schema.table` into the `<table_name>` slot.

## Observability — Lakeview dashboard (`dashboards/<pipeline_id>_lakeview.json`)
The Databricks-native equivalent of the other clouds' Grafana dashboard. Instead of
Prometheus/Pushgateway/Grafana (which don't exist here — there is no K8s), a **Databricks
Lakeview (AI/BI) dashboard** reads the Delta **`_audit` table** on the bootstrap **serverless SQL
warehouse** and renders the same metrics. The pipeline Terraform provisions it with
`databricks_dashboard` reading this exact JSON via `file_path` (see `terraform_databricks.md`).

> **GENERATION: CODE-OWNED.** This dashboard is rendered deterministically by
> `agents/codegen.py:render_lakeview_dashboard` from `databricks_target` in config —
> the architect no longer emits it at all. The skeleton below is the SPEC + the
> Medic's reference.

This used to be an LLM-emitted artifact. Historical instruction (now code-owned): emit it **verbatim**,
substituting only `<catalog>` / `<schema>` / `<table_name>` with the values from
`DELTA_DESTINATION.unity_catalog` (e.g. `multi_cloud_agent_workspace` / `raw` /
`pipe_sales_lakehouse`) — the audit table is `<catalog>.<schema>.<table_name>_audit`. Do NOT
rename the dataset/widget keys, do NOT switch `queryLines` to `query`, do NOT add a `$`-style
template variable (Lakeview is not Grafana). The grid is 6 columns wide.

> Safety net: `write_project_file` deterministically **rebuilds** this dashboard from the canonical
> structure, taking only the `_audit` table name from your output (the LLM tends to mangle the
> nested widget encodings into invalid JSON). So getting `<catalog>.<schema>.<table_name>_audit`
> right matters most — but still emit the full skeleton.

```json
{
  "datasets": [
    {
      "name": "ds_summary",
      "displayName": "Latest run",
      "queryLines": ["SELECT rows_processed, rows_rejected, duration_seconds, run_date, CASE WHEN (rows_processed + rows_rejected) > 0 THEN round(100.0 * rows_rejected / (rows_processed + rows_rejected), 1) ELSE 0 END AS rejection_rate_pct FROM <catalog>.<schema>.<table_name>_audit ORDER BY run_timestamp DESC LIMIT 1"]
    },
    {
      "name": "ds_trend",
      "displayName": "Per-run volume",
      "queryLines": ["SELECT run_date, 'processed' AS metric, rows_processed AS value FROM <catalog>.<schema>.<table_name>_audit UNION ALL SELECT run_date, 'rejected' AS metric, rows_rejected AS value FROM <catalog>.<schema>.<table_name>_audit"]
    },
    {
      "name": "ds_reasons",
      "displayName": "Rejections by reason (latest run)",
      "queryLines": ["SELECT reason, cnt FROM <catalog>.<schema>.<table_name>_audit LATERAL VIEW explode(rejected_by_reason) t AS reason, cnt WHERE run_timestamp = (SELECT MAX(run_timestamp) FROM <catalog>.<schema>.<table_name>_audit)"]
    }
  ],
  "pages": [
    {
      "name": "page_observability",
      "displayName": "Observability",
      "layout": [
        {
          "widget": {
            "name": "w_processed",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_summary", "fields": [{"name": "sum_rows_processed", "expression": "SUM(`rows_processed`)"}], "disaggregated": false}}],
            "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": {"fieldName": "sum_rows_processed", "displayName": "Records processed"}}, "frame": {"showTitle": true, "title": "Records Processed (latest run)"}}
          },
          "position": {"x": 0, "y": 0, "width": 2, "height": 3}
        },
        {
          "widget": {
            "name": "w_rejected",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_summary", "fields": [{"name": "sum_rows_rejected", "expression": "SUM(`rows_rejected`)"}], "disaggregated": false}}],
            "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": {"fieldName": "sum_rows_rejected", "displayName": "Records rejected"}}, "frame": {"showTitle": true, "title": "Records Rejected (latest run)"}}
          },
          "position": {"x": 2, "y": 0, "width": 2, "height": 3}
        },
        {
          "widget": {
            "name": "w_rate",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_summary", "fields": [{"name": "max_rejection_rate_pct", "expression": "MAX(`rejection_rate_pct`)"}], "disaggregated": false}}],
            "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": {"fieldName": "max_rejection_rate_pct", "displayName": "Rejection rate %"}}, "frame": {"showTitle": true, "title": "Rejection Rate % (latest run)"}}
          },
          "position": {"x": 4, "y": 0, "width": 2, "height": 3}
        },
        {
          "widget": {
            "name": "w_duration",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_summary", "fields": [{"name": "max_duration_seconds", "expression": "MAX(`duration_seconds`)"}], "disaggregated": false}}],
            "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": {"fieldName": "max_duration_seconds", "displayName": "Duration (s)"}}, "frame": {"showTitle": true, "title": "Run Duration s (latest run)"}}
          },
          "position": {"x": 0, "y": 3, "width": 3, "height": 3}
        },
        {
          "widget": {
            "name": "w_lastrun",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_summary", "fields": [{"name": "max_run_date", "expression": "MAX(`run_date`)"}], "disaggregated": false}}],
            "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": {"fieldName": "max_run_date", "displayName": "Last run date"}}, "frame": {"showTitle": true, "title": "Last Run Date"}}
          },
          "position": {"x": 3, "y": 3, "width": 3, "height": 3}
        },
        {
          "widget": {
            "name": "w_trend",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_trend", "fields": [{"name": "run_date", "expression": "`run_date`"}, {"name": "metric", "expression": "`metric`"}, {"name": "sum_value", "expression": "SUM(`value`)"}], "disaggregated": false}}],
            "spec": {"version": 3, "widgetType": "line", "encodings": {"x": {"fieldName": "run_date", "scale": {"type": "categorical"}, "displayName": "Run date"}, "y": {"fieldName": "sum_value", "scale": {"type": "quantitative"}, "displayName": "Records"}, "color": {"fieldName": "metric", "scale": {"type": "categorical"}, "displayName": "Metric"}}, "frame": {"showTitle": true, "title": "Records Processed vs Rejected over time"}}
          },
          "position": {"x": 0, "y": 6, "width": 6, "height": 6}
        },
        {
          "widget": {
            "name": "w_reasons",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_reasons", "fields": [{"name": "reason", "expression": "`reason`"}, {"name": "sum_cnt", "expression": "SUM(`cnt`)"}], "disaggregated": false}}],
            "spec": {"version": 3, "widgetType": "bar", "encodings": {"x": {"fieldName": "reason", "scale": {"type": "categorical"}, "displayName": "Reason"}, "y": {"fieldName": "sum_cnt", "scale": {"type": "quantitative"}, "displayName": "Rejected rows"}}, "frame": {"showTitle": true, "title": "Rejections by Reason (latest run)"}}
          },
          "position": {"x": 0, "y": 12, "width": 6, "height": 6}
        }
      ]
    }
  ]
}
```

## Hard rules
- No `to_parquet`, no `cloud_get()`, no `os.getenv("POSTGRES_DB_*"/"MYSQL_DB_*")`, no `create_engine`,
  no `push_to_gateway`, no Trino/Grafana code.
- **No `requirements.txt`** — the Databricks cluster runtime provides pyspark + delta, and the
  source JDBC driver is attached as a Maven library by the Terraform. The artifacts are the Spark
  script, the Unity Catalog DDL, and the Lakeview dashboard JSON (above) — nothing else. (If a
  `requirements.txt` is emitted anyway `write_project_file` drops it — a pyspark-only file is the
  databricks signature.)
- The audit-table write is **MANDATORY** — a run that writes data but no audit row is non-compliant.
