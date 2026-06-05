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

The bootstrap (`bootstrap/gcp/`) already owns the pipeline's **service account**, its
**Workload Identity binding** (GKE KSA → GSA) and a **project-level** `storage.objectAdmin`
grant. The pipeline Terraform must therefore **reference** the service account via a `data`
source and **create only** the pipeline-specific data-plane resources. Re-creating the
service account (same `account_id`) or its Workload Identity binding fails on `apply` with a
`409 "already exists"` conflict.

### Reference via `data` (NEVER re-create — bootstrap owns these)
| Data source | Purpose |
|---|---|
| `data "google_service_account"` | The bootstrap-created pipeline SA (`account_id` = `var.service_account_id`). Its GKE Workload Identity binding is ALSO bootstrap-owned — never create a `google_service_account_iam_member` for it. |

### Create (pipeline-owned data plane)
| Resource | Purpose |
|---|---|
| `google_storage_bucket` | Core GCS data bucket |
| `google_storage_bucket_iam_member` | IAM: `roles/storage.objectAdmin` binding the bootstrap SA to THIS bucket |

**Never** generate `google_service_account` or `google_service_account_iam_member` (Workload
Identity binding) in the pipeline Terraform — they are bootstrap-owned (`bootstrap/gcp/iam.tf`).

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

**`var.project_id` is the GCP PROJECT ID** — the cloud project that owns the bucket, SA and Artifact Registry — supplied in context as `CLOUD_SETUP.gcp_project_id`. It is **NOT** the pipeline `project_id` (that value is the pipeline_id / dashboard label). Putting the pipeline_id here makes the bucket and the `data "google_service_account"` lookup target a non-existent project and Terraform apply fails. See §5 for the `terraform.tfvars` mapping.

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

Never use static service account JSON keys in Kubernetes pods. The pipeline uses **GKE
Workload Identity** (GKE K8s service account → GCP service account), but the service account
AND its Workload Identity binding are **bootstrap-owned** — the pipeline only references the
SA and binds it to the new bucket.

### 3.1 GCP Service Account (bootstrap-owned — reference only)
The SA is created by `bootstrap/gcp/iam.tf`. Reference it via a `data` source — **never**
create a `google_service_account` here (a duplicate `account_id` fails with `409 already exists`):
```hcl
data "google_service_account" "pipeline" {
  account_id = var.service_account_id
  project    = var.project_id
}
```

### 3.2 Storage IAM Binding (the ONE pipeline-owned IAM resource)
Binds the bootstrap SA to THIS bucket:
```hcl
resource "google_storage_bucket_iam_member" "pipeline" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_service_account.pipeline.email}"
}
```

### 3.3 Workload Identity Binding (bootstrap-owned — do NOT create)
The GKE-OIDC → GCP-SA binding (member
`PROJECT_ID.svc.id.goog[analytics/<k8s_service_account_name>]`) is provisioned by
`bootstrap/gcp/iam.tf`. The pipeline Terraform must **never** generate a
`google_service_account_iam_member` for it — it already exists and a duplicate fails with a
`409` conflict.

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

`variables.tf` MUST declare exactly these five — **copy the block verbatim**. Every line begins with the keyword `variable`; dropping the keyword (e.g. `k8s_service_account_name { type = string }`) is an `Unsupported block type` error that fails `terraform init`:
```hcl
variable "project_id"             { type = string }
variable "region"                 { type = string }
variable "bucket_name"            { type = string }
variable "service_account_id"     { type = string }
variable "k8s_service_account_name" { type = string }
```

`terraform.tfvars` MUST be populated with concrete values from context — never placeholders. Map each value precisely:

| tfvars key | Source in context | Example |
|---|---|---|
| `project_id` | `CLOUD_SETUP.gcp_project_id` — the **GCP project id**, NOT the pipeline `project_id` | `multi-cloud-self-healing-agent` |
| `region` | `CLOUD_SETUP.region` | `europe-west3` |
| `bucket_name` | `CLOUD_SETUP.bucket_name` | `global-marketing-insights-data` |
| `service_account_id` | `CLOUD_SETUP.service_account_id` | `global-mkt-insights-sa` |
| `k8s_service_account_name` | `CLOUD_SETUP.k8s_service_account_name` | `global-mkt-insights-sa` |

```hcl
# ❌ WRONG — pipeline_id used as the GCP project → bucket/SA target a non-existent project
project_id = "pipe_mkt_global_to_gcp"
# ✅ CORRECT — the real GCP project id from CLOUD_SETUP.gcp_project_id
project_id = "multi-cloud-self-healing-agent"
```

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
  value = data.google_service_account.pipeline.email
}

output "service_account_id" {
  value = data.google_service_account.pipeline.id
}

output "project_id" {
  value = var.project_id
}
```

---

## 7. TRINO GCS CONNECTOR CONFIGURATION

The Trino Hive catalog ConfigMap (`hive-catalog-config`) is **NOT** part of this Terraform
standard — it is owned by `k8s_deployment_rules.md` §8.4, which is the single source of truth.
Two non-negotiable invariants from there: the ConfigMap data key is **always `hive.properties`**
(catalog name = `hive`, never `gcs.properties`/catalog `gcs` — that would break every
`hive.<schema>.<table>` reference the SQL/script use), and GCP uses a file metastore over GCS
with Workload Identity (`hive.metastore=file`, `hive.metastore.catalog.dir=gs://<bucket>/metastore/`,
`hive.gcs.use-access-token=false`, no JSON key). Generate it per §8.4, not from here.

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
