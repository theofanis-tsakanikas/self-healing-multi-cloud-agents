# MISSION OBJECTIVE: EU Sales Data Pipeline to Databricks Delta Lake (Idempotent Execution)

> **PLATFORM NOTE — this is a Databricks pipeline, NOT a Kubernetes/Trino pipeline.**
> There is NO Dockerfile, NO Kubernetes, NO Trino, NO Grafana/Prometheus, NO parquet.
> Compute is a Databricks **jobs cluster**; storage is **Delta Lake**; the catalog is
> **Unity Catalog**; observability is a **Delta audit table** (the Databricks-native
> equivalent of the Prometheus metrics the other clouds emit).

**## 1. INFRASTRUCTURE & SECURITY (TERRAFORM)**
* **Standards Inheritance:** Apply `{{target_infra_config}}` (databricks.yaml) — host_cloud, jobs cluster spec, Unity Catalog, Delta storage.
* **Workspace:** The Databricks workspace, jobs cluster, SQL warehouse, metastore and `raw` schema are provisioned by `bootstrap/databricks/`. The pipeline-level Terraform creates ONLY the **`databricks_job`** that runs this pipeline's Spark task on the existing cluster (`cluster_id` from bootstrap).
* **Backend:** Terraform state on the host_cloud (`aws_setup.state_bucket` / `state_key`).

**## 2. DATA ENGINEERING (PYSPARK + DELTA)**
* **Source:** Read EU Sales data from Postgres (per `{{source_config}}`) via Spark JDBC. Credentials come from a **Databricks secret scope** (`dbutils.secrets`) — NEVER `cloud_get()`, NEVER `os.getenv()` for the password.
* **Transformations:** Apply ALL `{{business_rules_config}}` quality_standards as Spark DataFrame operations (DROP_RECORD → `.filter()`, EXCLUDE_AND_LOG → filter + log count, DEFAULT_VALUE → `.withColumn(..., when(...))`, FLAG_AS_SUSPICIOUS → `.withColumn("is_suspicious", ...)`).
* **Write:** `df.write.format("delta").mode("overwrite")` (or MERGE for idempotency) into the Unity Catalog table `{{databricks_target.catalog}}.{{databricks_target.schema}}.{{databricks_target.table_name}}`, partitioned by `run_date`.
* **Idempotency:** If the `run_date` partition already exists in the Delta table, log and return before writing.

**## 3. OBSERVABILITY — DELTA AUDIT TABLE (MANDATORY)**
* After a successful write, append exactly one row to the audit table
  `{{databricks_target.catalog}}.{{databricks_target.schema}}.{{databricks_target.table_name}}_audit`
  with columns: `run_timestamp` (timestamp), `run_date` (string), `rows_processed` (long), `rows_rejected` (long), `duration_seconds` (double), `rejected_by_reason` (map<string,long>).
* This is the Databricks-native equivalent of the Prometheus gauges — every pipeline records its own health, consistently across all clouds.

**## 4. DATA CATALOG (UNITY CATALOG DDL)**
* Generate `sql/setup_unity_catalog.sql`: `CREATE CATALOG IF NOT EXISTS`, `CREATE SCHEMA IF NOT EXISTS`, and `CREATE TABLE IF NOT EXISTS ... USING DELTA` for BOTH the data table and the `_audit` table. Use Unity Catalog 3-part names. NO `external_location`/`PARTITIONED_BY = ARRAY[...]`/`FORMAT = 'PARQUET'` (those are Trino-Hive syntax).

**## 5. CI/CD (DATABRICKS CLI)**
* The deploy workflow uses the **Databricks CLI** (`databricks bundle` / `databricks jobs`) authenticated via `DATABRICKS_HOST` + `DATABRICKS_TOKEN`. NO docker build, NO `kubectl`, NO ECR.

**## 6. CONSTRAINTS**
* English for all code/comments. No PII in logs.
* Resource naming derived from `pipeline_id`, never `project_id`.
