# MISSION OBJECTIVE: PIPE_MKT_GLOBAL_TO_GCP

**Natural Language Input:** Global marketing pipeline to GCP

---

## 🏗️ 1. ARCHITECT SCOPE (DATA LOGIC)

**DATA PIPELINE (PYTHON):**
- Source: `mysql` database — table from `DATA_SOURCE.table` in your context (never guess or invent a table name)
- Output format: parquet/snappy
- Destination URI: `gs://global-marketing-insights-data/processed/`
- Idempotency: check `gs://global-marketing-insights-data/processed/run_date=<today>/` before writing. If data exists, exit 0.
- Save script to: `scripts/pipe_mkt_global_to_gcp.py`

**BUSINESS RULES:**
  - See `TRANSFORMATION_LOGIC` in your context — this is the authoritative and complete rules list. Do not infer or skip rules based on this task description.

**CATALOG & OBSERVABILITY:**
- Trino DDL: `sql/setup_trino.sql` — external table at `gs://global-marketing-insights-data/processed/` with `run_date` partitioning
- Trino target: `hive.marketing_global.pipe_mkt_global_to_gcp`
- Grafana: `dashboards/monitoring_specs.json` with 60-minute Data Silence alert

---

## 🛠️ 2. INFRA SCOPE (DEPLOYMENT & AUTOMATION)

**TERRAFORM:** Deploy GCP storage (GCS) and identity resources (Service Account + Workload Identity).
**K8S:** Deploy namespaces, Trino, Grafana, Prometheus + Pipeline CronJob (hourly schedule from `gcp_setup.schedule`).
**CI/CD:** `/.github/workflows/pipe_mkt_global_to_gcp_pipeline.yml`

---

## 🔒 3. GLOBAL CONSTRAINTS

- All DB credentials via `cloud_get()` — never `os.getenv()` for host/user/password/db. GitHub Secrets are for CI/CD auth only (Artifact Registry, cloud CLI).
- Every agent MUST query the Vector Store for domain standards before writing.
- Final signal: `echo "Deployment Complete"`.
