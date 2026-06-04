# MISSION OBJECTIVE: PIPE_CRM_US_TO_AZURE

**Natural Language Input:** US CRM pipeline to Azure (PII-sensitive customer data)

---

## 🏗️ 1. ARCHITECT SCOPE (DATA LOGIC)

**DATA PIPELINE (PYTHON):**
- Source: `postgres` database — table from `DATA_SOURCE.table` in your context (never guess or invent a table name)
- Output format: parquet/snappy
- Destination URI: `abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/`
- Idempotency: check `abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/run_date=<today>/` before writing. If data exists, log and return (never `exit()`).
- PII (`pii_sensitive: true`): BEFORE applying the business rules, SHA-256 **hash** the customer name column and **mask** the email and phone columns (e.g. `a***@b.com`). Apply to every row — this is an unconditional transform, not a quality rule.
- Save script to: `scripts/pipe_crm_us_to_azure.py`

**BUSINESS RULES:**
  - See `TRANSFORMATION_LOGIC` in your context — this is the authoritative and complete rules list. Do not infer or skip rules based on this task description.

**CATALOG & OBSERVABILITY:**
- Trino DDL: `sql/setup_trino.sql` — external table at `abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/` with `run_date` partitioning
- Trino target: `hive.crm_us.pipe_crm_us_to_azure`
- Grafana: `dashboards/monitoring_specs.json` — the standard five panels + 60-minute Data Silence alert

---

## 🛠️ 2. INFRA SCOPE (DEPLOYMENT & AUTOMATION)

**TERRAFORM:** Provision the Azure ADLS Gen2 storage account + private container + the `Storage Blob Data Contributor` role assignment. The resource group, managed identity and AKS→identity federation are **bootstrap-owned** — reference them via `data` sources, NEVER create them. Encryption is platform-managed — no Key Vault / CMK.
**K8S:** Deploy namespaces, Trino, Grafana, Prometheus + the Pipeline Job. The Job MUST use `serviceAccountName: us-crm-insights-sa` and carry the `azure.workload.identity/use: "true"` pod label.
**CI/CD:** `/.github/workflows/pipe_crm_us_to_azure_pipeline.yml`

---

## 🔒 3. GLOBAL CONSTRAINTS

- All DB credentials via `cloud_get()` — never `os.getenv()` for host/user/password/db. GitHub Secrets are for CI/CD auth only (ACR, cloud CLI).
- Every agent MUST query the Vector Store for domain standards before writing.
- **Compliance:** pipeline logs must NEVER contain raw PII — only metadata and anonymized statistics.
- Final signal: `echo "Deployment Complete"`.
