terraform {
  required_providers {
    databricks = { source = "databricks/databricks", version = "~> 1.0" }
    aws        = { source = "hashicorp/aws", version = "~> 5.0" }   # to read the DB creds from SSM
  }
  backend "s3" {
    bucket = "multi-cloud-agent-bootstrap-state"
    key    = "terraform/sales-lakehouse/terraform.tfstate"
    region = "eu-central-1"
  }
}

provider "databricks" {
  auth_type = "pat"
}

provider "aws" {
  region = "eu-central-1"
}