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

# ── Source RDS Postgres (the Lakehouse OLTP source) ──────────────────────────
# The password is auto-generated (database.tf) and published to SSM (ssm.tf) — never a var.
variable "db_name" {
  description = "Database name for the Lakehouse source (POSTGRES_DB_NAME). Distinct from AWS eu_sales."
  type        = string
  default     = "lakehouse_raw"
}

variable "db_username" {
  description = "Master username for the Lakehouse source Postgres (POSTGRES_DB_USER)."
  type        = string
  default     = "postgres"
}

variable "rds_allowed_cidrs" {
  description = "CIDRs allowed to reach the source RDS on 5432 (demo: open; protected by the password)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
