# ── Enable required APIs ──────────────────────────────────────────────────────

resource "google_project_service" "container" {
  service            = "container.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "iam" {
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "sqladmin" {
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

# GKE (nodes, networking, the compute default service account data source below) needs
# the Compute Engine API. It is NOT reliably auto-enabled by container.googleapis.com.
resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

# Workload Identity (GKE pods → GSA, and the GitHub-Actions OIDC federation) needs the
# Security Token Service + IAM Service Account Credentials APIs.
resource "google_project_service" "sts" {
  service            = "sts.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "iamcredentials" {
  service            = "iamcredentials.googleapis.com"
  disable_on_destroy = false
}

# ── GKE Autopilot Cluster ─────────────────────────────────────────────────────
# Autopilot manages node lifecycle automatically; no node pool configuration needed.
# Workload Identity is enabled by default on Autopilot clusters.

resource "google_container_cluster" "main" {
  provider = google-beta

  name     = var.gke_cluster_name
  location = var.region

  # Autopilot: fully managed node lifecycle
  enable_autopilot = true

  # Workload Identity is enabled automatically on Autopilot
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Release channel for automatic Kubernetes upgrades
  release_channel {
    channel = "REGULAR"
  }

  depends_on = [google_project_service.container, google_project_service.compute]

  lifecycle {
    ignore_changes = [min_master_version]
  }
}

# ── Artifact Registry: pull secret for GKE nodes ────────────────────────────
# GKE Autopilot pulls images from Artifact Registry using the node service account.
# Grant it the Artifact Registry Reader role.

data "google_compute_default_service_account" "default" {
  depends_on = [google_project_service.compute]
}

resource "google_artifact_registry_repository_iam_member" "gke_pull" {
  repository = google_artifact_registry_repository.main.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${data.google_compute_default_service_account.default.email}"
}
