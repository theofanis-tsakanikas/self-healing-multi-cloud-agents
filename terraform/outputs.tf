output "storage_account_name" {
  value = azurerm_storage_account.data.name


output "container_name" {
  value = azurerm_storage_container.data.name
}
  value = azurerm_storage_container.data.name
}

output "managed_identity_client_id" {
  value = data.azurerm_user_assigned_identity.pipeline.client_id
}
  value = data.azurerm_user_assigned_identity.pipeline.client_id
}

output "managed_identity_id" {
  value = data.azurerm_user_assigned_identity.pipeline.id
}
  value = data.azurerm_user_assigned_identity.pipeline.id
}
}

output "resource_group_name" {
  value = data.azurerm_resource_group.main.name
}
  value = data.azurerm_resource_group.main.name
}