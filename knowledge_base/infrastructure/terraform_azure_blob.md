# STANDARD: TERRAFORM AZURE ADLS GEN2 & BACKEND
This standard defines the mandatory configuration for Azure Storage (ADLS Gen2) resources and Terraform state management.

## FILE RESPONSIBILITIES (strictly enforced)

| File | Contains | Must NOT contain |
|---|---|---|
| `providers.tf` | `terraform { backend "azurerm" {} }` + `provider "azurerm" {}` | resource blocks |
| `main.tf` | resource blocks ONLY | `terraform {}`, `provider {}` |
| `variables.tf` | `variable` declarations | anything else |
| `outputs.tf` | `output` declarations | anything else |
| `terraform.tfvars` | variable values | HCL blocks |

**CRITICAL: If `providers.tf` already exists, `main.tf` must NEVER include a `terraform {}` or `provider {}` block.**

---

## MANDATORY RESOURCE CHECKLIST
Every `main.tf` generated for an Azure ADLS Gen2 pipeline MUST contain ALL of the following resources:

| Resource | Purpose |
|---|---|
| `azurerm_resource_group` | Container for all resources |
| `azurerm_storage_account` | Core ADLS Gen2 storage (hierarchical namespace enabled) |
| `azurerm_storage_container` | Data container within the storage account |
| `azurerm_user_assigned_identity` | Managed identity for workload identity federation |
| `azurerm_role_assignment` | RBAC: Storage Blob Data Contributor on the storage account |
| `azurerm_federated_identity_credential` | Binds AKS service account to managed identity |

`outputs.tf` MUST export exactly: `storage_account_name`, `container_name`, `managed_identity_client_id`, `managed_identity_id`, `resource_group_name`.

---

## 1. TERRAFORM BACKEND CONFIGURATION

Use the **AzureRM backend** for state storage. All backend values must be **string literals** — no `var.*` in backend blocks.

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
    resource_group_name  = "multi-cloud-agent-tfstate-rg"
    storage_account_name = "multicloudagenttfstate"
    container_name       = "tfstate"
    key                  = "azure/<pipeline_id>/terraform.tfstate"
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}
```

**State Prerequisite:** The state storage account and container must be created manually once before running `terraform init`. They are NOT managed by this Terraform configuration to avoid circular dependency.

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
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = true
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

### 3.1 User-Assigned Managed Identity
```hcl
resource "azurerm_user_assigned_identity" "pipeline" {
  name                = var.managed_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags = { project_id = var.project_id }
}
```

### 3.2 RBAC Assignment (Storage Blob Data Contributor)
```hcl
resource "azurerm_role_assignment" "pipeline_storage" {
  scope                = azurerm_storage_account.data.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.pipeline.principal_id
}
```

### 3.3 Federated Identity Credential
Binds the AKS Kubernetes service account to the managed identity.
```hcl
resource "azurerm_federated_identity_credential" "pipeline" {
  name                = "${var.project_id}-federated-credential"
  resource_group_name = azurerm_resource_group.main.name
  parent_id           = azurerm_user_assigned_identity.pipeline.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = var.aks_oidc_issuer_url
  subject             = "system:serviceaccount:analytics:${var.k8s_service_account_name}"
}
```

`var.aks_oidc_issuer_url` must come from the AKS bootstrap output (`oidc_issuer_url`).

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

`variables.tf` MUST declare:
```hcl
variable "resource_group_name" { type = string }
variable "location"             { type = string }
variable "storage_account_name" { type = string }
variable "container_name"       { type = string }
variable "project_id"           { type = string }
variable "subscription_id"      { type = string }
variable "managed_identity_name"{ type = string }
variable "k8s_service_account_name" { type = string }
variable "aks_oidc_issuer_url"  { type = string }
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
  value = azurerm_user_assigned_identity.pipeline.client_id
}

output "managed_identity_id" {
  value = azurerm_user_assigned_identity.pipeline.id
}

output "resource_group_name" {
  value = azurerm_resource_group.main.name
}
```

---

## 7. LIFECYCLE & ENCRYPTION

- **Versioning:** Always enabled via `blob_properties.versioning_enabled = true`
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
