variable "region" {
  default = "eu-central-1"
}

variable "state_bucket" {
  default = "multi-cloud-agent-tf-state-bucket"
}

variable "lock_table" {
  default = "terraform-state-lock"
}

variable "ecr_repo_name" {
  default = "eu-sales-pipeline-repo"
}

variable "eks_cluster_name" {
  default = "multi-cloud-agent-cluster"
}

variable "eks_version" {
  default = "1.35"
}

variable "cluster_role_name" {
  default = "eks-cluster-role"
}

variable "node_role_name" {
  default = "eks-node-role"
}

variable "rds_db_name" {
  description = "Database name inside the RDS instance (POSTGRES_DB_NAME)"
  default     = "sales_raw"
}

variable "rds_username" {
  description = "Master username for the RDS instance (POSTGRES_DB_USER)"
  default     = "postgres"
}

variable "rds_password" {
  description = "Master password for the RDS instance (POSTGRES_DB_PASSWORD)"
  type        = string
  sensitive   = true
}

variable "rds_allowed_cidrs" {
  description = "CIDR blocks allowed to connect to RDS on port 5432 (add your local IP)"
  type        = list(string)
}

variable "pipeline_iam_arn" {
  description = "ARN of the IAM user/role used by the CI pipeline. Gets cluster-admin access to EKS."
  type        = string
}
