variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "resource_group_name" {
  description = "Name of the resource group for all bootstrap resources"
  default     = "multi-cloud-agent-rg"
}

variable "location" {
  description = "Azure region for all resources"
  default     = "eastus"
}

variable "aks_cluster_name" {
  description = "Name of the AKS cluster"
  default     = "multi-cloud-agent-aks"
}

variable "kubernetes_version" {
  description = "Kubernetes version for AKS"
  default     = "1.30"
}

variable "acr_name" {
  description = "Azure Container Registry name (globally unique, alphanumeric only)"
  default     = "multicloudagentacr"
}

variable "state_storage_account" {
  description = "Storage account for Terraform pipeline states (shared by all Azure pipelines)"
  default     = "multicloudagenttfstate"
}

variable "state_container_name" {
  description = "Blob container for Terraform pipeline states"
  default     = "tfstate"
}

variable "db_server_name" {
  description = "PostgreSQL Flexible Server name (globally unique)"
  default     = "multi-cloud-agent-pg"
}

variable "db_name" {
  description = "Database name inside the PostgreSQL server"
  default     = "crm_raw"
}

variable "db_username" {
  description = "Administrator username for PostgreSQL"
  default     = "pgadmin"
}

variable "db_password" {
  description = "Administrator password for PostgreSQL"
  type        = string
  sensitive   = true
}

variable "db_allowed_cidrs" {
  description = "CIDR blocks allowed to connect to PostgreSQL on port 5432"
  type        = list(string)
  default     = []
}

variable "pipeline_object_id" {
  description = "Object ID of the GitHub Actions service principal that needs AKS access"
  type        = string
  default     = ""
}
