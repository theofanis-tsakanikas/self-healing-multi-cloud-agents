variable "workspace_name" {
  description = "Name of the Databricks workspace to create."
  type        = string
  default     = "multi-cloud-agent-workspace"
}

variable "region" {
  description = "Cloud region where the workspace and supporting resources are deployed."
  type        = string
  default     = "eu-central-1"
}

variable "cloud_type" {
  description = "Target cloud provider for the workspace. Valid values: aws, azure, gcp."
  type        = string
  default     = "aws"

  validation {
    condition     = contains(["aws", "azure", "gcp"], var.cloud_type)
    error_message = "cloud_type must be one of: aws, azure, gcp."
  }
}

variable "bucket_name" {
  description = "S3 bucket name used as the DBFS root and Unity Catalog external location."
  type        = string
}

variable "account_id" {
  description = "Databricks account ID. Sourced from DATABRICKS_ACCOUNT_ID env var by the provider."
  type        = string
  sensitive   = true
}

variable "metastore_name" {
  description = "Name of the Unity Catalog metastore to create or attach."
  type        = string
  default     = "multi-cloud-agent-metastore"
}
