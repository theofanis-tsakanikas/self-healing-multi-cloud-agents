terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "multi-cloud-agent-tfstate"
    prefix = "gcp/etl-gcp-pipeline-insights/terraform.tfstate"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}