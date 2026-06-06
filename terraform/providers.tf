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
    prefix = "gcp/global-marketing-insights/terraform.tfstate"
  }
}
