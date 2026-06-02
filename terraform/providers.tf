terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }

  backend "azurerm" {
    resource_group_name  = "multi-cloud-agent-rg"
    storage_account_name = "multicloudagenttfstate"
    container_name       = "tfstate"
    key                  = "azure/us-crm-insights/terraform.tfstate"
  }
}

provider "azurerm" {
  features {}
}