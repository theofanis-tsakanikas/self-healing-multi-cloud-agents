output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "aks_cluster_endpoint" {
  value     = azurerm_kubernetes_cluster.main.kube_config[0].host
  sensitive = true
}

output "aks_oidc_issuer_url" {
  description = "Use this as aks_oidc_issuer_url in pipeline Terraform modules"
  value       = azurerm_kubernetes_cluster.main.oidc_issuer_url
}

output "acr_login_server" {
  description = "Docker registry URL — use with: az acr login --name <acr_name>"
  value       = azurerm_container_registry.main.login_server
}

output "tfstate_storage_account" {
  description = "Set as AZURE_STATE_STORAGE_ACCOUNT in GitHub vars"
  value       = azurerm_storage_account.tfstate.name
}

output "tfstate_container_name" {
  value = azurerm_storage_container.tfstate.name
}

output "crm_managed_identity_client_id" {
  description = "Annotate us-crm-insights-sa K8s service account with this client ID"
  value       = azurerm_user_assigned_identity.crm_pipeline.client_id
}

output "crm_managed_identity_principal_id" {
  value = azurerm_user_assigned_identity.crm_pipeline.principal_id
}

output "db_host" {
  description = "Set as CRM_DB_HOST in GitHub Secrets"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "db_port" {
  value = 5432
}

output "db_name" {
  description = "Set as CRM_DB_NAME in GitHub Secrets"
  value       = azurerm_postgresql_flexible_server_database.main.name
}

output "db_username" {
  description = "Set as CRM_DB_USER in GitHub Secrets"
  value       = azurerm_postgresql_flexible_server.main.administrator_login
}

output "resource_group_name" {
  value = azurerm_resource_group.main.name
}
