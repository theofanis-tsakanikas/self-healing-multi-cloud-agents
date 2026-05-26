terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.75"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Separate meta bucket — created once manually, never managed by Terraform.
  # Stores only the bootstrap state, avoiding circular dependency with
  # the self-healing-agent-tf-state-bucket that bootstrap itself creates.
  backend "s3" {
    bucket = "multi-cloud-agent-bootstrap-state"
    key    = "terraform.tfstate"
    region = "eu-central-1"
  }
}

provider "aws" {
  region = "eu-central-1"
}
