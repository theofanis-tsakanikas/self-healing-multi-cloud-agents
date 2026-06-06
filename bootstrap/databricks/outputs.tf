output "workspace_url" {
  description = "URL of the provisioned Databricks workspace."
  value       = databricks_mws_workspaces.this.workspace_url
}

output "workspace_id" {
  description = "Numeric ID of the Databricks workspace."
  value       = databricks_mws_workspaces.this.workspace_id
}

output "cluster_id" {
  description = "ID of the jobs cluster created in the workspace."
  value       = databricks_cluster.jobs.id
}

output "warehouse_id" {
  description = "ID of the SQL Warehouse created in the workspace."
  value       = databricks_sql_endpoint.main.id
}

output "catalog_name" {
  description = "Name of the Unity Catalog catalog created for this project."
  value       = databricks_catalog.main.name
}

output "source_db_endpoint" {
  description = "Endpoint of the Lakehouse source Postgres — set this as the POSTGRES_DB_HOST variable."
  value       = aws_db_instance.source_db.address
}
