"""
Regression: the LLM intermittently emits a GCP providers.tf with ONLY the terraform{} block and
drops `provider "google" { project = var.project_id ... }`. terraform apply then fails with
"project: required field is not set" on google_storage_bucket (observed on global_marketing,
2026-06-17). validate_generated_code must flag the missing provider block at WRITE time so the
medic re-adds it before the apply.
"""
import os
import tempfile

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from agents.tools import validate_generated_code


def _validate_providers(src: str) -> str:
    d = tempfile.mkdtemp()
    f = os.path.join(d, "providers.tf")
    with open(f, "w") as fh:
        fh.write(src)
    return str(validate_generated_code.invoke({"filename": f}))


# The exact broken providers.tf the user pasted — terraform{} only, NO provider block.
_MISSING = '''terraform {
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
}'''

_CORRECT = _MISSING + '''

provider "google" {
  project = var.project_id
  region  = var.region
}'''


def test_missing_google_provider_block_is_flagged():
    out = _validate_providers(_MISSING)
    assert "VALIDATION FAILED" in out
    assert "provider" in out.lower() and "google" in out.lower()


def test_providers_with_google_block_passes():
    out = _validate_providers(_CORRECT)
    assert "VALIDATION FAILED" not in out
