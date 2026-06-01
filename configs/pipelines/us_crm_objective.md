# MISSION OBJECTIVE: US CRM Data Pipeline to Azure Blob Storage (Idempotent Execution)

**## 1. INFRASTRUCTURE & SECURITY (TERRAFORM)**

**Azure Storage & Encryption**
* **Standards Inheritance:** Apply all technical standards defined in `{{target_infra_config}}` (azure_blob.yaml), specifically ensuring **Hierarchical Namespace (ADLS Gen2)** and **StorageV2** kind.
* **Storage Account:** Provision the account named `{{azure_setup.storage_account_name}}` in region `{{azure_setup.region}}`.
* **Container:** Create the blob container `{{azure_setup.container_name}}` within the account.
* **Security (PII):** * **Access Level:** Ensure the container is strictly **Private** as per infra standards.
    * **Encryption:** Rely on Azure Storage **platform-managed encryption at rest** (always on). Customer-Managed Keys are out of scope — bootstrap provisions no Key Vault, so do NOT generate `azurerm_key_vault` / CMK resources.
* **Idempotency:** Verify that the existing infrastructure matches both the project-specific values and the `{{target_infra_config}}` standards.

**Identity & Access Management (Azure & K8s)**
* **Managed Identity (bootstrap-owned — reference only):** The User-Assigned Managed Identity `{{azure_setup.managed_identity_name}}` is created by bootstrap. Reference it via a Terraform `data` source — do NOT create an `azurerm_user_assigned_identity`.
* **RBAC Permissions:** Assign the **'Storage Blob Data Contributor'** role to that identity, scoped to the data storage account this pipeline provisions.
* **Kubernetes Integration:** Create a Service Account named `{{azure_setup.k8s_service_account_name}}` in namespace `{{azure_setup.k8s_namespace}}` carrying the workload-identity annotation/label. The AKS→identity federation is provisioned by bootstrap — do NOT create an `azurerm_federated_identity_credential`.

**## 2. DATA ENGINEERING & PII HANDLING (PYTHON)**
* **Base Image:** Use the shared `Dockerfile` including drivers specified in `{{target_infra_config}}` (`azure-storage-blob`, `psycopg2-binary`).
* **Extraction:** Extract customer data from Postgres (per `{{source_config}}`).
* **Anonymization (Compliance):** Since `pii_sensitive` is `true`, apply SHA-256 **hashing** to the customer name column and **masking** to the email and phone columns (e.g. `a***@b.com`). This is an unconditional transform applied to every row — it is NOT a `quality_standards` rule.
* **Output:** Write the anonymized dataset as **Parquet** (snappy compression), per the infra standards.
* **Upload:** Write to the destination injected as `DESTINATION_URI` (the standard `…/processed/` prefix), partitioned by `run_date=YYYY-MM-DD/` per the python standard. NEVER hardcode a bucket path or add `project_id` as a path component.

**## 3. SHARED SERVICES INTEGRATION (TRINO & GRAFANA)**
* **Trino Validation:**
    * Use the `target_uri_pattern` from `{{target_infra_config}}` to construct the ABFS path.
    * Path (the table `external_location`): `abfss://{{azure_setup.container_name}}@{{azure_setup.storage_account_name}}.dfs.core.windows.net/processed/` — the stable `…/processed/` parent (matches `LOGICAL_DESTINATION.uri`), NEVER a `project_id`-suffixed folder, so Trino discovers every `run_date=` partition.
* **Grafana Monitoring:**
    * Connect to `{{shared_services.grafana.url}}`.
    * Dashboard: Update/Create **"US CRM Business Insights"** following the Grafana standard EXACTLY — the mandatory **five panels** (Record Count, Last Success, Rejection Rate, Run Duration, Rejections by Reason), one per emitted metric. Do NOT invent custom panels (e.g. "Retention Rate") that are not backed by an emitted Prometheus metric.
    * Alert: the standard 60-minute Data Silence rule (severity critical).

**## 4. DEPLOYMENT ENGINEERING (KUBERNETES)**
* **K8s Job:** Deploy as a Kubernetes Job named `us-crm-insights-job-{{project_id}}`.
* **Security Context:** The Job must use `serviceAccountName: {{azure_setup.k8s_service_account_name}}` to leverage Azure Workload Identity.

**## 5. CONSTRAINTS**
* Use English for all code comments.
* Resource naming: Prefix all temporary K8s resources with `us-crm-{{project_id}}`.
* **Compliance:** Pipeline logs must **never** contain raw PII data. Only metadata and anonymized statistics are allowed.
* **Config Merging:** The Agent must merge `infra/azure_blob.yaml` (technical standards) with the project-specific YAML (values). Project-specific values always override infra standards in case of conflict.
* Ensure compatibility with GitHub Actions for CI/CD by referencing resources via environment variables.