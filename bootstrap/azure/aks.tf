resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

# Resolve the latest non-preview Kubernetes version actually supported in this region.
# Pinning a literal (e.g. "1.30") breaks when Azure moves it to LTS-only.
data "azurerm_kubernetes_service_versions" "current" {
  location        = var.location
  include_preview = false
}

resource "azurerm_kubernetes_cluster" "main" {
  name                = var.aks_cluster_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.aks_cluster_name
  kubernetes_version  = data.azurerm_kubernetes_service_versions.current.latest_version

  # Enable OIDC issuer — required for workload identity federation
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name       = "system"
    node_count = 2
    vm_size    = "Standard_DS2_v2"

    upgrade_settings {
      max_surge = "10%"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    load_balancer_sku = "standard"
  }

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

# Grant AKS pull access to ACR so nodes can pull pipeline images
resource "azurerm_role_assignment" "aks_acr_pull" {
  principal_id                     = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
  role_definition_name             = "AcrPull"
  scope                            = azurerm_container_registry.main.id
  skip_service_principal_aad_check = true
}

# Optional: grant CI pipeline service principal cluster admin access
resource "azurerm_role_assignment" "pipeline_aks_admin" {
  count                = var.pipeline_object_id != "" ? 1 : 0
  principal_id         = var.pipeline_object_id
  role_definition_name = "Azure Kubernetes Service Cluster Admin Role"
  scope                = azurerm_kubernetes_cluster.main.id
}
