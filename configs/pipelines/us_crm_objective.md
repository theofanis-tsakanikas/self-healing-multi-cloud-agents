# MISSION OBJECTIVE: US CRM Data Pipeline to Azure Blob Storage (Idempotent Execution)

**## 1. INFRASTRUCTURE & SECURITY (TERRAFORM)**

**Azure Storage & Encryption**
* **Standards Inheritance:** Apply all technical standards defined in `{{target_infra_config}}` (azure_blob.yaml), specifically ensuring **Hierarchical Namespace (ADLS Gen2)** and **StorageV2** kind.
* **Storage Account:** Provision the account named `{{azure_setup.storage_account_name}}` in region `{{azure_setup.region}}`.
* **Container:** Create the blob container `{{azure_setup.container_name}}` within the account.
* **Security (PII):** * **Access Level:** Ensure the container is strictly **Private** as per infra standards.
    * **Encryption:** Implement **Customer-Managed Keys (CMK)** via Azure Key Vault as specified in the configuration.
* **Idempotency:** Verify that the existing infrastructure matches both the project-specific values and the `{{target_infra_config}}` standards.

**Identity & Access Management (Azure & K8s)**
* **Managed Identity:** Create a User-Assigned Managed Identity named `{{azure_setup.managed_identity_name}}`.
* **RBAC Permissions:** Assign the **'Storage Blob Data Contributor'** role to this identity, scoped strictly to `{{azure_setup.container_name}}`.
* **Kubernetes Integration:** * Create a Service Account named `{{azure_setup.k8s_service_account_name}}` in namespace `{{azure_setup.k8s_namespace}}`.
    * Configure **Azure AD Workload Identity** (federation) to bind the K8s SA with the Azure Managed Identity for passwordless authentication.

**## 2. DATA ENGINEERING & PII HANDLING (PYTHON)**
* **Base Image:** Use the shared `Dockerfile` including drivers specified in `{{target_infra_config}}` (`azure-storage-blob`, `psycopg2-binary`).
* **Extraction:** Extract customer data from Postgres (per `{{source_config}}`).
* **Anonymization (Compliance):** Since `pii_sensitive` is `true`, apply **masking** or **hashing** to email and phone number columns as defined in `{{business_rules_config}}`.
* **Output:** Convert the anonymized dataset to the format specified in infra standards (**{{target_infra_config.format_standard}}**).
* **Upload:** Store the output in Azure Blob Storage under the path: `crm-processed/{{project_id}}/`.

**## 3. SHARED SERVICES INTEGRATION (TRINO & GRAFANA)**
* **Trino Validation:**
    * Use the `target_uri_pattern` from `{{target_infra_config}}` to construct the ABFS path.
    * Path: `abfss://{{azure_setup.container_name}}@{{azure_setup.storage_account_name}}.dfs.core.windows.net/crm-processed/{{project_id}}/`.
* **Grafana Monitoring:**
    * Connect to `{{shared_services.grafana.url}}`.
    * Dashboard: Update/Create **"US CRM Business Insights"**.
    * Setup metrics for **"Customer Retention Rate"** and **"Regional Sync Health"**.
    * Set an alert for any data volume drop larger than 20% compared to the previous run.

**## 4. DEPLOYMENT ENGINEERING (KUBERNETES)**
* **K8s Job:** Deploy as a Kubernetes Job named `us-crm-insights-job-{{project_id}}`.
* **Security Context:** The Job must use `serviceAccountName: {{azure_setup.k8s_service_account_name}}` to leverage Azure Workload Identity.

**## 5. CONSTRAINTS**
* Use English for all code comments.
* Resource naming: Prefix all temporary K8s resources with `us-crm-{{project_id}}`.
* **Compliance:** Pipeline logs must **never** contain raw PII data. Only metadata and anonymized statistics are allowed.
* **Config Merging:** The Agent must merge `infra/azure_blob.yaml` (technical standards) with the project-specific YAML (values). Project-specific values always override infra standards in case of conflict.
* Ensure compatibility with GitHub Actions for CI/CD by referencing resources via environment variables.