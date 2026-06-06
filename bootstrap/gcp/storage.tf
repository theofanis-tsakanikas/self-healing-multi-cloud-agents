# ── GCS: Terraform state bucket for pipeline runs ─────────────────────────────

resource "google_storage_bucket" "tfstate" {
  name                        = var.state_bucket_name
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Ephemeral demo: let `terraform destroy` (and destroy.yml) remove the bucket even with
  # versioned pipeline state inside. Without this a versioned, non-empty bucket blocks teardown.
  force_destroy = true

  versioning {
    enabled = true
  }

  labels = {
    project   = "multi-cloud-agent"
    purpose   = "terraform-state"
    managedby = "terraform-bootstrap"
  }

  depends_on = [google_project_service.container]
}

# ── Artifact Registry: Docker image repository ────────────────────────────────

resource "google_artifact_registry_repository" "main" {
  location      = var.region
  repository_id = var.artifact_registry_name
  description   = "Pipeline Docker images for multi-cloud agent"
  format        = "DOCKER"

  labels = {
    project   = "multi-cloud-agent"
    managedby = "terraform-bootstrap"
  }

  depends_on = [google_project_service.artifactregistry]
}
