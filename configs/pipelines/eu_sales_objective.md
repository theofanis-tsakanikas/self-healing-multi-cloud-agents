# MISSION OBJECTIVE: PIPE_EU_SALES_TO_S3

**Natural Language Input:** EU sales pipeline to AWS

---

## 🏗️ 1. ARCHITECT SCOPE (DATA LOGIC)

**DATA PIPELINE (PYTHON):**
- Source: `postgres` database, table `raw_orders`
- Output format: parquet/snappy
- Destination URI: `s3://eu-sales-insights-data/processed/`
- Idempotency: check `s3://eu-sales-insights-data/processed/run_date=<today>/` before writing. If data exists, exit 0.
- Save script to: `scripts/pipe_eu_sales_to_s3.py`

**BUSINESS RULES:**
  - No explicit rules. Apply general data quality standards.

**CATALOG & OBSERVABILITY:**
- Trino DDL: `sql/setup_trino.sql` — external table at `s3://eu-sales-insights-data/processed/` with `run_date` partitioning
- Trino target: `hive.sales_eu.pipe_eu_sales_to_s3`
- Grafana: `dashboards/monitoring_specs.json` with 60-minute Data Silence alert

---

## 🛠️ 2. INFRA SCOPE (DEPLOYMENT & AUTOMATION)

**TERRAFORM:** Deploy AWS storage and identity/IAM resources.
**K8S:** Deploy namespaces, Trino, Grafana, Prometheus + Pipeline Job.
**CI/CD:** `/.github/workflows/pipe_eu_sales_to_s3_pipeline.yml`

---

## 🔒 3. GLOBAL CONSTRAINTS

- All credentials via `os.getenv()` / GitHub Secrets — zero hardcoding.
- Every agent MUST query the Vector Store for domain standards before writing.
- Final signal: `echo "Deployment Complete"`.
