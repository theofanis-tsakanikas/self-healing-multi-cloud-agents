terraform {
  required_version = ">= 1.6"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # Remote state (host_cloud = aws) so the workspace/Unity-Catalog footprint survives across
  # CI runs and can be torn down by destroy.yml. Reuses the AWS bootstrap state bucket.
  # If a workspace was already applied with local state, run `terraform init -migrate-state`.
  backend "s3" {
    bucket = "multi-cloud-agent-bootstrap-state"
    key    = "bootstrap/databricks/terraform.tfstate"
    region = "eu-central-1"
  }
}

# Databricks account-level provider (for Unity Catalog metastore and workspace creation).
# Auth via environment variables: DATABRICKS_ACCOUNT_ID, DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET.
provider "databricks" {
  alias      = "accounts"
  host       = "https://accounts.cloud.databricks.com"
  account_id = var.account_id
}

# Databricks workspace-level provider (for cluster, warehouse, catalog objects).
provider "databricks" {
  alias = "workspace"
  host  = databricks_mws_workspaces.this.workspace_url
}

provider "aws" {
  region = var.region
}
