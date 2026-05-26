# STANDARD: TERRAFORM GCP CLOUD STORAGE & BACKEND
This standard defines the mandatory configuration for GCP Cloud Storage (GCS) resources and Terraform state management.

## FILE RESPONSIBILITIES (strictly enforced)

| File | Contains | Must NOT contain |
|---|---|---|
| `providers.tf` | `terraform { backend "gcs" {} }` + `provider "google" {}` | resource blocks |
| `main.tf` | resource blocks ONLY | `terraform {}`, `provider {}` |
| `variables.tf` | `variable` declarations | anything else |
| `outputs.tf` | `output` declarations | anything else |
| `terraform.tfvars` | variable values | HCL blocks |

**CRITICAL: If `providers.tf` already exists, `main.tf` must NEVER include a `terraform {}` or `provider {}` block.**

---

## MANDATORY RESOURCE CHECKLIST
Every `main.tf` generated for a GCP GCS pipeline MUST contain ALL of the following resources:

| Resource | Purpose |
|---|---|
| `google_storage_bucket` | Core GCS data bucket |
| `google_service_account` | Service account for the pipeline workload |
| `google_storage_bucket_iam_member` | IAM: roles/storage.objectAdmin on the bucket |
| `google_service_account_iam_member` | Workload identity binding (GKE SA → GSA) |
| `google_project_iam_member` | Optional: additional project-level permissions |

`outputs.tf` MUST export exactly: `bucket_name`, `bucket_url`, `service_account_email`, `service_account_id`, `project_id`.

---

## 1. TERRAFORM BACKEND CONFIGURATION

Use the **GCS backend** for state storage. All backend values must be **string literals**.

```hcl
terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "multi-cloud-agent-tfstate"
    prefix = "gcp/<pipeline_id>/terraform.tfstate"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
```

**State Prerequisite:** The GCS state bucket must be created manually once before `terraform init`. It is NOT managed by this Terraform configuration.

---

## 2. GCS BUCKET PROVISIONING

### 2.1 Core Bucket
- `storage_class`: `STANDARD`
- `uniform_bucket_level_access`: `true` — MANDATORY. Disables ACLs, uses IAM only. Never set to false.
- `public_access_prevention`: `"enforced"` — blocks all public access
- `versioning.enabled`: `true`
- **`force_destroy = false`** (omit entirely) — never allow accidental deletion of data buckets

```hcl
resource "google_storage_bucket" "data" {
  name                        = var.bucket_name
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
    condition {
      age = 90
    }
  }

  labels = {
    project_id = var.project_id
    managed_by = "terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
}
```

### 2.2 URI Pattern for Trino / Python
The destination URI for Parquet writes MUST follow this pattern:
```
gs://{bucket_name}/{path}/
```
Example: `gs://global-marketing-insights-data/processed/`

---

## 3. SERVICE ACCOUNT & WORKLOAD IDENTITY FEDERATION

Never use static service account JSON keys in Kubernetes pods. Use **GKE Workload Identity** (binds GKE K8s service account to a GCP service account).

### 3.1 GCP Service Account
```hcl
resource "google_service_account" "pipeline" {
  account_id   = var.service_account_id
  display_name = "${var.project_id} Pipeline Service Account"
  project      = var.project_id
}
```

### 3.2 Storage IAM Binding
```hcl
resource "google_storage_bucket_iam_member" "pipeline" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}
```

### 3.3 Workload Identity Binding (GKE SA → GCP SA)
```hcl
resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member = "serviceAccount:${var.project_id}.svc.id.goog[analytics/${var.k8s_service_account_name}]"
}
```

The member format `PROJECT_ID.svc.id.goog[NAMESPACE/KSA_NAME]` is the GKE Workload Identity binding pattern. Never deviate from it.

---

## 4. KUBERNETES SERVICE ACCOUNT (GKE WORKLOAD IDENTITY)

The `00_namespaces.yaml` MUST use this annotation for GKE workload identity:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <k8s_service_account_name>
  namespace: analytics
  annotations:
    iam.gke.io/gcp-service-account: "<service_account_email>"
```

The `<service_account_email>` is the GCP service account email from Terraform output: `${account_id}@${project_id}.iam.gserviceaccount.com`.

---

## 5. NAMING & TAGGING

`variables.tf` MUST declare:
```hcl
variable "project_id"             { type = string }
variable "region"                 { type = string }
variable "bucket_name"            { type = string }
variable "service_account_id"     { type = string }
variable "k8s_service_account_name" { type = string }
```

`terraform.tfvars` MUST be populated with concrete values — never placeholders.

---

## 6. MANDATORY OUTPUTS

```hcl
output "bucket_name" {
  value = google_storage_bucket.data.name
}

output "bucket_url" {
  value = google_storage_bucket.data.url
}

output "service_account_email" {
  value = google_service_account.pipeline.email
}

output "service_account_id" {
  value = google_service_account.pipeline.id
}

output "project_id" {
  value = var.project_id
}
```

---

## 7. TRINO GCS CONNECTOR CONFIGURATION

For Trino to read Parquet from GCS using the Hive connector, add this catalog ConfigMap to `configmaps.yaml`:

```properties
# gcs.properties
connector.name=hive
hive.metastore=file
hive.metastore.catalog.dir=gs://<bucket_name>/metastore/
hive.gcs.use-access-token=false
hive.gcs.json-key-file-path=/etc/trino/gcs-key.json
```

If using Workload Identity (recommended), omit `json-key-file-path` and add `hive.gcs.use-access-token=false` with the GKE workload identity annotation on the Trino pod.

---

## 8. LIFECYCLE & SECURITY

- **Versioning:** Always enabled
- **Uniform bucket-level access:** Always true (never use legacy ACLs)
- **Public access prevention:** Always `"enforced"`
- **Encryption:** Google-managed by default. For CMEK, add `google_kms_key_ring` + `google_kms_crypto_key` + set `encryption.default_kms_key_name` on the bucket.
- **Retention policy:** For compliance, optionally add:
```hcl
retention_policy {
  retention_period = 2592000  # 30 days in seconds
}
```
