data "azurerm_client_config" "current" {}

# ── Managed Identity: us-crm-insights pipeline ────────────────────────────────

resource "azurerm_user_assigned_identity" "crm_pipeline" {
  name                = "us-crm-insights-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  tags = {
    Project   = "multi-cloud-agent"
    Pipeline  = "us-crm-insights"
    ManagedBy = "terraform-bootstrap"
  }
}

# ── Federated Identity Credential (AKS OIDC → Managed Identity) ─────────────
# Binds the Kubernetes service account in the analytics namespace to the
# managed identity, enabling passwordless auth from pod to Azure Storage.

resource "azurerm_federated_identity_credential" "crm_pipeline" {
  name                = "us-crm-insights-federated"
  resource_group_name = azurerm_resource_group.main.name
  parent_id           = azurerm_user_assigned_identity.crm_pipeline.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject             = "system:serviceaccount:analytics:us-crm-insights-sa"
}

# ── Storage Account Contributor: tfstate (for agent-generated Terraform runs) ─

resource "azurerm_role_assignment" "tfstate_contributor" {
  scope                = azurerm_storage_account.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.crm_pipeline.principal_id
}

# ── Shared managed identity for NL/Streamlit-authored pipelines ───────────────
# The bootstrap pre-creates a dedicated identity per VALIDATED pipeline (crm above). A
# pipeline authored at runtime through the NL/Streamlit surface has a brand-new
# ServiceAccount + storage account that no pre-created identity federates to, so it could
# never reach Azure Storage. This SHARED identity closes that gap. UNLIKE AWS IRSA, an
# Azure federated credential subject must be an EXACT string (no wildcard), so every
# NL-authored pipeline uses a FIXED shared ServiceAccount name (`pipelines-insights-sa`)
# that this one credential trusts. The per-pipeline Terraform then binds THIS identity to
# its own data storage account (azurerm_role_assignment), and annotates the SA with this
# identity's client_id. (See _build_from_answers -> pipeline_managed_identity_name.)
# Demo trade-off (SECURITY.md): all NL pipelines share one identity + SA name.
resource "azurerm_user_assigned_identity" "pipelines_shared" {
  name                = "multi-cloud-pipelines-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  tags = {
    Project   = "multi-cloud-agent"
    Pipeline  = "nl-authored-shared"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "azurerm_federated_identity_credential" "pipelines_shared" {
  name                = "multi-cloud-pipelines-federated"
  resource_group_name = azurerm_resource_group.main.name
  parent_id           = azurerm_user_assigned_identity.pipelines_shared.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject             = "system:serviceaccount:analytics:pipelines-insights-sa"
}
