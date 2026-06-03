# STANDARD: TERRAFORM AZURE ADLS GEN2 & BACKEND
This standard defines the mandatory configuration for Azure Storage (ADLS Gen2) resources and Terraform state management.

## FILE RESPONSIBILITIES (strictly enforced)

| File | Contains | Must NOT contain |
|---|---|---|
| `providers.tf` | `terraform { backend "azurerm" {} }` + `provider "azurerm" {}` | resource blocks |
| `main.tf` | resource AND data blocks | `terraform {}`, `provider {}` |
| `variables.tf` | `variable` declarations | anything else |
| `outputs.tf` | `output` declarations | anything else |
| `terraform.tfvars` | variable values | HCL blocks |

**CRITICAL: If `providers.tf` already exists, `main.tf` must NEVER include a `terraform {}` or `provider {}` block.**

---

## MANDATORY RESOURCE CHECKLIST

The bootstrap (`bootstrap/azure/`) already owns the shared identity and the resource
group. The pipeline Terraform must therefore **reference** those via `data` sources and
**create only** the pipeline-specific data-plane resources. Re-creating a bootstrap-owned
resource (resource group, managed identity, federated credential) causes a 409
"already exists" conflict on `apply`.

### Reference via `data` (NEVER re-create — bootstrap owns these)
| Data source | Purpose |
|---|---|
| `data "azurerm_resource_group"` | The bootstrap resource group that holds the cluster, registry, DB and managed identity |
| `data "azurerm_user_assigned_identity"` | The pipeline's managed identity created by bootstrap (its AKS workload-identity federation is also bootstrap-owned) |

### Create (pipeline-owned data plane)
| Resource | Purpose |
|---|---|
| `azurerm_storage_account` | Core ADLS Gen2 storage (hierarchical namespace enabled) |
| `azurerm_storage_container` | Data container within the storage account |
| `azurerm_role_assignment` | RBAC: Storage Blob Data Contributor — binds the bootstrap managed identity to THIS data account |

**Never** generate `azurerm_resource_group`, `azurerm_user_assigned_identity`, or
`azurerm_federated_identity_credential` in the pipeline Terraform — they are bootstrap-owned.

`outputs.tf` MUST export exactly: `storage_account_name`, `container_name`, `managed_identity_client_id`, `managed_identity_id`, `resource_group_name`.

---

## 1. TERRAFORM BACKEND CONFIGURATION

Use the **AzureRM backend** for state storage. All backend values must be **string literals** — no `var.*` in backend blocks.

**The backend `key` MUST be the exact `state_key` from `CLOUD_SETUP`, copied verbatim — NEVER derived from `pipeline_id` or any other identifier.** The `key` is the per-pipeline state path and must be byte-identical on every run of this pipeline. If the `key` differs between runs, Terraform reads an EMPTY state file and `apply` tries to re-create resources that already exist (e.g. the storage account) → fails with a `409 "already exists"` conflict, while the real state is orphaned under the old key. The project convention derives `state_key` from `project_name` (e.g. `us-crm-insights`), NOT from `pipeline_id` (`pipe_crm_us_to_azure`) — so deriving the key yourself produces the WRONG path. Copy `CLOUD_SETUP.state_key` exactly.

```hcl
terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }

  backend "azurerm" {
    resource_group_name  = "multi-cloud-agent-rg"
    storage_account_name = "multicloudagenttfstate"
    container_name       = "tfstate"
    key                  = "azure/us-crm-insights/terraform.tfstate"   # = CLOUD_SETUP.state_key
  }
}

provider "azurerm" {
  features {}
}
```

**`providers.tf` MUST contain BOTH blocks — the `terraform {}` block AND the `provider "azurerm" { features {} }` block.** The provider block is NOT optional: omitting it (or its `features {}`) makes `terraform apply` fail with `Invalid provider configuration` / `Insufficient features blocks: At least 1 "features" blocks are required`. Never generate only the `terraform {}` block and stop. (Auth itself comes from the ARM_* env vars the CI exports — `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`, `ARM_SUBSCRIPTION_ID`, `ARM_TENANT_ID` — for both the backend and the provider; never hardcode `subscription_id`.)

**Backend RG note:** `resource_group_name` MUST be `multi-cloud-agent-rg` — that is where
bootstrap creates the `multicloudagenttfstate` state storage account. Any other RG name
(e.g. a separate `…-tfstate-rg`) makes `terraform init` fail with "storage account not found".

**State Prerequisite:** The state storage account and container must be created manually once before running `terraform init`. They are NOT managed by this Terraform configuration to avoid circular dependency.

---

## 1.5 DATA SOURCES — BOOTSTRAP-OWNED REFERENCES

The resource group and the managed identity are created by `bootstrap/azure/`. The
pipeline references them by name — it must never re-create them (doing so triggers a 409
"already exists" error on `apply`). Declare these `data` blocks in `main.tf`:

```hcl
data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

data "azurerm_user_assigned_identity" "pipeline" {
  name                = var.managed_identity_name
  resource_group_name = var.resource_group_name
}
```

---

## 2. STORAGE ACCOUNT PROVISIONING (ADLS GEN2)

### 2.1 Core Storage Account
- `account_tier`: `Standard`
- `account_replication_type`: `LRS` (dev) or `GRS` (prod)
- `account_kind`: `StorageV2`
- **`is_hns_enabled = true`**: This enables ADLS Gen2 (hierarchical namespace). Never omit it.
- `min_tls_version`: `TLS1_2`
- Public access: `allow_nested_items_to_be_public = false`

```hcl
resource "azurerm_storage_account" "data" {
  name                            = var.storage_account_name
  resource_group_name             = data.azurerm_resource_group.main.name
  location                        = data.azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  blob_properties {
    # Blob versioning is NOT compatible with is_hns_enabled = true (ADLS Gen2): Azure
    # rejects the account with "versioning_enabled can't be true when is_hns_enabled is
    # true". HNS is mandatory for abfss://, so do NOT set versioning_enabled here.
    # Blob soft-delete (below) IS supported on HNS accounts and covers recoverability.
    delete_retention_policy {
      days = 7
    }
  }

  tags = {
    project_id = var.project_id
    ManagedBy  = "terraform"
  }
}
```

### 2.2 Storage Container
```hcl
resource "azurerm_storage_container" "data" {
  name                  = var.container_name
  storage_account_name  = azurerm_storage_account.data.name
  container_access_type = "private"
}
```

### 2.3 URI Pattern for Trino / Python
The destination URI for Parquet writes MUST follow this pattern:
```
abfss://{container_name}@{storage_account_name}.dfs.core.windows.net/{path}/
```
Example: `abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/`

---

## 3. MANAGED IDENTITY & WORKLOAD IDENTITY FEDERATION

Never use static storage account keys or connection strings in Kubernetes pods. Use **Azure Workload Identity** (OIDC federation between AKS and Azure AD).

### 3.1 Managed Identity (bootstrap-owned — reference only)
The managed identity is created by `bootstrap/azure/iam.tf`. Reference it via the
`data "azurerm_user_assigned_identity" "pipeline"` source declared in section 1.5 —
**never** create an `azurerm_user_assigned_identity` here.

### 3.2 RBAC Assignment (Storage Blob Data Contributor)
This is the ONE identity-related resource the pipeline owns: it binds the bootstrap
managed identity to THIS data account.
```hcl
resource "azurerm_role_assignment" "pipeline_storage" {
  scope                = azurerm_storage_account.data.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_user_assigned_identity.pipeline.principal_id
}
```

### 3.3 Federated Identity Credential (bootstrap-owned — do NOT create)
The AKS-OIDC → managed-identity federation (subject
`system:serviceaccount:analytics:<k8s_service_account_name>`) is provisioned by
`bootstrap/azure/iam.tf`. The pipeline Terraform must **never** generate an
`azurerm_federated_identity_credential` — it already exists and a duplicate fails with a
409 conflict.

---

## 4. KUBERNETES SERVICE ACCOUNT (AKS WORKLOAD IDENTITY)

The `00_namespaces.yaml` MUST use these annotations for AKS workload identity:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <k8s_service_account_name>
  namespace: analytics
  annotations:
    azure.workload.identity/client-id: "<managed_identity_client_id>"
  labels:
    azure.workload.identity/use: "true"
```

The pod/job spec MUST include `azure.workload.identity/use: "true"` as a label:
```yaml
spec:
  template:
    metadata:
      labels:
        azure.workload.identity/use: "true"
```

---

## 5. NAMING & TAGGING

`variables.tf` MUST declare only what the pipeline actually consumes. The RG and managed
identity are looked up by name via `data` sources, so `location`, `subscription_id`,
`aks_oidc_issuer_url` and `k8s_service_account_name` are NOT needed here:
```hcl
variable "resource_group_name"  { type = string }
variable "storage_account_name" { type = string }
variable "container_name"       { type = string }
variable "project_id"           { type = string }
variable "managed_identity_name"{ type = string }
```

`terraform.tfvars` MUST be populated with concrete values — never placeholders.

---

## 6. MANDATORY OUTPUTS

```hcl
output "storage_account_name" {
  value = azurerm_storage_account.data.name
}

output "container_name" {
  value = azurerm_storage_container.data.name
}

output "managed_identity_client_id" {
  value = data.azurerm_user_assigned_identity.pipeline.client_id
}

output "managed_identity_id" {
  value = data.azurerm_user_assigned_identity.pipeline.id
}

output "resource_group_name" {
  value = data.azurerm_resource_group.main.name
}
```

---

## 7. LIFECYCLE & ENCRYPTION

- **Versioning:** NOT available on ADLS Gen2 (HNS) accounts — `versioning_enabled = true` is rejected by Azure when `is_hns_enabled = true`. Never set it here; rely on blob soft-delete (`delete_retention_policy`) for recoverability.
- **Soft delete:** Set `delete_retention_policy.days = 7` minimum
- **Encryption:** Azure Storage encrypts at rest by default (Microsoft-managed keys). For Customer-Managed Keys (CMK), add `azurerm_key_vault` + `azurerm_storage_account_customer_managed_key` resources.
- **`prevent_destroy = true`** is MANDATORY on `azurerm_storage_account` to prevent accidental deletion.

```hcl
resource "azurerm_storage_account" "data" {
  # ...
  lifecycle {
    prevent_destroy = true
  }
}
```
