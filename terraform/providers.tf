terraform {
  required_providers {
    databricks = { source = "databricks/databricks", version = "~> 1.0" }
    aws        = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  backend "s3" {
    bucket = "multi-cloud-agent-bootstrap-state"
    key    = "terraform/sales-dbx-pipeline-data-lakehouse/terraform.tfstate"
    region = "eu-central-1"
  }
}

provider "databricks" {
  auth_type = "oauth-m2m"
}

provider "aws" {
  region = "eu-central-1"
}