data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

data "azurerm_user_assigned_identity" "pipeline" {
  name                = var.managed_identity_name
  resource_group_name = var.resource_group_name
}

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
    delete_retention_policy {
      days = 7
    }
  }

  tags = {
    project_id = var.project_id
    ManagedBy  = "terraform"
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "data" {
  name                  = var.container_name
  storage_account_name  = azurerm_storage_account.data.name
  container_access_type = "private"
}

resource "azurerm_storage_data_lake_gen2_path" "processed" {
  path               = "processed"
  filesystem_name    = azurerm_storage_container.data.name
  storage_account_id = azurerm_storage_account.data.id
  resource           = "directory"
}

resource "azurerm_role_assignment" "pipeline_storage" {
  scope                = azurerm_storage_account.data.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_user_assigned_identity.pipeline.principal_id
}