terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.50"
    }
  }

  # State stored in a storage account created manually once before `terraform init`.
  # Avoids circular dependency with resources this bootstrap manages.
  backend "azurerm" {
    resource_group_name  = "multi-cloud-agent-bootstrap-rg"
    storage_account_name = "multicloudbootstrapstate"
    container_name       = "tfstate"
    key                  = "bootstrap/azure/terraform.tfstate"
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id = var.subscription_id
}

provider "azuread" {}
