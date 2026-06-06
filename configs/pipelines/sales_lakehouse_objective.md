# MISSION OBJECTIVE: PIPE_SALES_LAKEHOUSE

**Natural Language Input:** Sales data to a Databricks Delta Lakehouse (Unity Catalog)

> **PLATFORM NOTE — Databricks, NOT a Kubernetes/Trino pipeline.** No Dockerfile, no Kubernetes,
> no Trino, no Grafana/Prometheus, no parquet. Compute = Databricks jobs cluster; storage = Delta
> Lake; catalog = Unity Catalog; observability = a Delta audit table (the Databricks-native
> equivalent of the Prometheus gauges the other clouds emit).

---

## 🏗️ 1. ARCHITECT SCOPE (DATA LOGIC)

**DATA PIPELINE (PYSPARK):**
- Source: `postgres` database — table from `DATA_SOURCE.table` in your context (never guess or invent a table name). Read via Spark JDBC.
- Credentials: `dbutils.secrets` from the pipeline's secret scope — never `cloud_get()`, never `os.getenv()`.
- Output: Delta, written to the Unity Catalog table `DELTA_DESTINATION.unity_catalog.table` from your context, partitioned by `run_date`.
- Idempotency: if the `run_date` partition already exists in the Delta table, log and return before writing.
- Save script to: `scripts/pipe_sales_lakehouse.py`

**BUSINESS RULES:**
  - See `TRANSFORMATION_LOGIC` in your context — this is the authoritative and complete rules list. Do not infer or skip rules based on this task description.

**CATALOG & OBSERVABILITY:**
- Unity Catalog DDL: `sql/setup_unity_catalog.sql` — `USING DELTA` for BOTH the data table and the `_audit` table (3-part names). NO `external_location` / `PARTITIONED_BY = ARRAY[...]` / `FORMAT = 'PARQUET'` (that is Trino-Hive syntax).
- Delta audit table (MANDATORY): append one row per run with `run_timestamp`, `run_date`, `rows_processed`, `rows_rejected`, `duration_seconds`, `rejected_by_reason`. This is the Databricks-native equivalent of the Prometheus gauges.

---

## 🛠️ 2. INFRA SCOPE (DEPLOYMENT & AUTOMATION)

**TERRAFORM:** `databricks_job` (Spark task on the existing bootstrap jobs cluster) + `databricks_secret_scope` for the DB password. NO storage bucket, NO IAM, NO Kubernetes — the workspace, cluster, and Unity Catalog are provisioned by `bootstrap/databricks/`.
**CI/CD:** `/.github/workflows/pipe_sales_lakehouse_pipeline.yml` — Databricks CLI authenticated via `DATABRICKS_HOST` + `DATABRICKS_TOKEN`. NO docker build, NO `kubectl`, NO ECR.

---

## 🔒 3. GLOBAL CONSTRAINTS

- DB credentials via `dbutils.secrets` — never `cloud_get()` / `os.getenv()`. GitHub Secrets are for CI/CD auth only (`DATABRICKS_HOST` / `DATABRICKS_TOKEN`).
- Every agent MUST query the Vector Store for domain standards before writing.
- Resource naming derived from `pipeline_id`, never `project_id`.
- Final signal: `echo "Deployment Complete"`.
