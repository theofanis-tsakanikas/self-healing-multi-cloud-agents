variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  default     = "europe-west3"
}

variable "gke_cluster_name" {
  description = "GKE Autopilot cluster name"
  default     = "multi-cloud-agent-gke"
}

variable "artifact_registry_name" {
  description = "Artifact Registry repository name for pipeline Docker images"
  default     = "multi-cloud-agent-repo"
}

variable "state_bucket_name" {
  description = "GCS bucket name for Terraform pipeline states (shared by all GCP pipelines)"
  default     = "multi-cloud-agent-tfstate"
}

variable "db_instance_name" {
  description = "Cloud SQL instance name"
  default     = "multi-cloud-agent-mysql"
}

variable "db_name" {
  description = "Database name inside Cloud SQL"
  default     = "marketing_raw"
}

variable "db_user" {
  description = "Database user for the marketing pipeline"
  default     = "pipeline_user"
}

variable "db_password" {
  description = "Password for the Cloud SQL database user"
  type        = string
  sensitive   = true
}

variable "pipeline_github_repo" {
  description = "GitHub repo in owner/repo format for Workload Identity Federation"
  type        = string
  default     = ""
}
