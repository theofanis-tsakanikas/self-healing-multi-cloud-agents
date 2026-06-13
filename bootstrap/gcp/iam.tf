data "google_project" "current" {}

# ── Service Account: global-marketing-insights pipeline ──────────────────────

resource "google_service_account" "marketing_pipeline" {
  account_id   = "global-mkt-insights-sa"
  display_name = "Global Marketing Insights Pipeline SA"
  project      = var.project_id

  depends_on = [google_project_service.iam]
}

# ── GCS IAM: object admin on the marketing data bucket ───────────────────────
# Created by the pipeline agent at run time; binding is added proactively here
# so the service account is ready when the agent provisions the GCS bucket.

resource "google_project_iam_member" "marketing_storage_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.marketing_pipeline.email}"
}

# ── Workload Identity Binding ─────────────────────────────────────────────────
# Allows the Kubernetes service account in the analytics namespace to impersonate
# the GCP service account without static JSON keys.

resource "google_service_account_iam_member" "workload_identity_binding" {
  service_account_id = google_service_account.marketing_pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[analytics/global-mkt-insights-sa]"

  depends_on = [google_container_cluster.main]
}

# ── Shared SA for NL/Streamlit-authored pipelines ─────────────────────────────
# The bootstrap pre-creates a dedicated SA per VALIDATED pipeline (marketing above). A
# pipeline authored at runtime through the NL/Streamlit surface has a brand-new
# ServiceAccount + bucket that no pre-created SA's Workload Identity binding trusts, so it
# could never reach GCS. This SHARED SA closes that gap. Like Azure (and unlike AWS IRSA), a
# GCP Workload Identity binding member is EXACT (no wildcard), so every NL-authored pipeline
# uses a FIXED shared KSA (`pipelines-insights-sa`) that this one binding trusts. The
# per-pipeline Terraform binds this SA to its own bucket; project-level objectAdmin below
# already covers any `*-insights-data` bucket. (See _build_from_answers -> pipeline_service_account_*.)
# Demo trade-off (SECURITY.md): all NL pipelines share one SA + KSA name.
resource "google_service_account" "pipelines_shared" {
  account_id   = "pipelines-insights-sa"
  display_name = "Shared NL/Streamlit-authored Pipelines SA"
  project      = var.project_id

  depends_on = [google_project_service.iam]
}

resource "google_project_iam_member" "pipelines_shared_storage_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.pipelines_shared.email}"
}

resource "google_service_account_iam_member" "pipelines_shared_workload_identity" {
  service_account_id = google_service_account.pipelines_shared.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[analytics/pipelines-insights-sa]"

  depends_on = [google_container_cluster.main]
}

# ── GitHub Actions Workload Identity Federation (optional) ───────────────────
# Allows GitHub Actions to authenticate with GCP using OIDC (no JSON key needed).
# Only created if pipeline_github_repo is set.

resource "google_iam_workload_identity_pool" "github" {
  count                     = var.pipeline_github_repo != "" ? 1 : 0
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions Pool"
  project                   = var.project_id

  depends_on = [google_project_service.iam]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = var.pipeline_github_repo != "" ? 1 : 0
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  project                            = var.project_id

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_condition = "assertion.repository == '${var.pipeline_github_repo}'"
}

resource "google_service_account_iam_member" "github_actions_binding" {
  count              = var.pipeline_github_repo != "" ? 1 : 0
  service_account_id = google_service_account.marketing_pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.pipeline_github_repo}"
}
