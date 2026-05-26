# MISSION OBJECTIVE: Global Marketing Data Pipeline to GCP (Idempotent Execution)

**## 1. INFRASTRUCTURE & STORAGE (TERRAFORM)**

**GCP Storage (GCS)**
* **Standards Inheritance:** Apply all technical standards defined in `{{target_infra_config}}` (gcp_bucket.yaml), specifically **Uniform Bucket-Level Access** and **Public Access Prevention**.
* **Provisioning:** Create the Google Cloud Storage (GCS) Bucket named `{{gcp_setup.bucket_name}}` in region `{{gcp_setup.region}}`.
* **Security & Compliance:** * **Versioning:** Must be **Enabled** as per infra standards to protect against accidental deletions.
    * **Lifecycle:** Implement the lifecycle rules (e.g., Nearline transition) defined in `{{target_infra_config}}`.
* **Idempotency:** If the bucket exists, verify that versioning is enabled and encryption/access settings match the org policy.

**Identity & Access Management (GCP & K8s)**
* **Authentication Method:** Use **{{target_infra_config.auth_method}}** (Workload Identity Federation) to eliminate the use of static JSON keys.
* **Service Account:** Create a GCP Service Account named `{{gcp_setup.k8s_service_account_name}}`.
* **IAM Permissions:** Grant the `roles/storage.objectAdmin` role to this Service Account, restricted strictly to `{{gcp_setup.bucket_name}}`.
* **Kubernetes Integration:** Bind the Kubernetes Service Account `{{gcp_setup.k8s_service_account_name}}` in namespace `{{gcp_setup.k8s_namespace}}` to the GCP Service Account using Workload Identity.

**## 2. DATA ENGINEERING & LOGIC (PYTHON)**
* **Base Image:** Use the shared `Dockerfile` with required drivers (`google-cloud-storage`, `mysql-connector-python`).
* **Extraction:** Connect to MySQL (per `{{source_config}}`) using incremental loading based on the `last_updated` timestamp.
* **Processing:** * Clean campaign logs using `{{business_rules_config}}`.
    * Deduplicate entries based on `lead_id`.
* **Output:** Convert data to **{{target_infra_config.data_format}}** with **{{target_infra_config.compression_type}}** compression.
* **Upload:** Path: `gs://{{gcp_setup.bucket_name}}/processed/{{project_id}}/`.

**## 3. SHARED SERVICES INTEGRATION (TRINO & GRAFANA)**
* **Trino Validation:**
    * Use the `target_uri_pattern` from `{{target_infra_config}}` to construct the GS path.
    * Check if the schema `{{shared_services.trino.schema}}` exists in catalog `{{shared_services.trino.catalog}}`.
    * Define an **External Table** pointing to: `gs://{{gcp_setup.bucket_name}}/processed/{{project_id}}/`.
* **Grafana Monitoring:**
    * Dashboard: Update/Create **"Real-time Marketing Monitor"**.
    * Metrics: **"Cost per Click (CPC) Trend"** and **"Hourly Ingestion Rate"**.
    * Set an alert for 60-minute data silence.

**## 4. DEPLOYMENT & SCHEDULING (KUBERNETES)**
* **Orchestration:** Deploy as a **Kubernetes CronJob** named `mkt-cron-{{project_id}}`.
* **Schedule:** Set to `"0 * * * *"` (every hour).
* **Security Context:** Ensure the Pod uses the K8s Service Account bound to the GCP identity.
* **Resource Limits:** Explicitly set CPU/Memory limits to ensure cluster stability during hourly bursts.

**## 5. CONSTRAINTS**
* Use English for all code comments.
* Resource naming: All dynamic K8s resources must include the `{{project_id}}` suffix.
* **Scalability:** The Python logic must handle a 2x increase in log volume using memory-efficient streaming.
* **Config Merging:** The Agent must merge `infra/gcp_bucket.yaml` (standards) with the project-specific YAML (values). Project values always override standards.
* Ensure compatibility with GitHub Actions for CI/CD by referencing resources via environment variables.