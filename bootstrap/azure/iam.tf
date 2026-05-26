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
