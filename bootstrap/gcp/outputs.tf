output "gke_cluster_name" {
  value = google_container_cluster.main.name
}

output "gke_cluster_endpoint" {
  value     = google_container_cluster.main.endpoint
  sensitive = true
}

output "artifact_registry_url" {
  description = "Docker registry URL — use with: gcloud auth configure-docker <region>-docker.pkg.dev"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_name}"
}

output "tfstate_bucket_name" {
  description = "Set as GCP_TFSTATE_BUCKET in GitHub vars"
  value       = google_storage_bucket.tfstate.name
}

output "marketing_service_account_email" {
  description = "Annotate global-mkt-insights-sa K8s service account with this email"
  value       = google_service_account.marketing_pipeline.email
}

output "pipeline_service_account_email" {
  description = "Shared SA for NL/Streamlit-authored pipelines — annotate pipelines-insights-sa with it"
  value       = google_service_account.pipelines_shared.email
}

output "pipeline_service_account_id" {
  description = "Shared SA account_id for NL/Streamlit-authored pipelines (var.service_account_id)"
  value       = google_service_account.pipelines_shared.account_id
}

output "workload_identity_provider" {
  description = "Set as GCP_WORKLOAD_IDENTITY_PROVIDER in GitHub Secrets (if using OIDC)"
  value       = var.pipeline_github_repo != "" ? google_iam_workload_identity_pool_provider.github[0].name : "not configured"
}

output "db_host" {
  description = "Set as MYSQL_DB_HOST in GitHub Secrets"
  value       = google_sql_database_instance.main.public_ip_address
}

output "db_port" {
  value = 3306
}

output "db_name" {
  description = "Set as MYSQL_DB_NAME in GitHub Secrets"
  value       = google_sql_database.main.name
}

output "db_user" {
  description = "Set as MYSQL_DB_USER in GitHub Secrets"
  value       = google_sql_user.pipeline.name
}

output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}
