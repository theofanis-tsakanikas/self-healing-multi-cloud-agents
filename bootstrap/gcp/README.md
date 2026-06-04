# GCP Bootstrap — Operator Runbook

Provisions the shared GCP baseline for the `global_marketing` pipeline:
**GKE Autopilot + Cloud SQL (MySQL) + Artifact Registry + GCS state bucket + the pipeline
Service Account & Workload Identity.**

Most of this is Terraform (`terraform apply`, driven by `run_agent.yml` → `bootstrap_cloud: gcp`).
The items below are the **irreducible one-time, human-only prerequisites** — they need an
account with Owner + a billing account, or they cannot self-bootstrap from Terraform. Do them
once, in order, before the first bootstrap run.

---

## 1. One-time manual prerequisites (human, Owner)

1. **Enable billing** on the project (Console → Billing → link a billing account).
   Without it, every API enablement and resource create fails.

2. **Enable the two "chicken-egg" APIs** — Terraform CANNOT enable these itself, because the
   google provider needs them already active to read the project and enable any other API:
   ```bash
   gcloud auth login
   gcloud config set project <GCP_PROJECT_ID>
   gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com
   ```
   (All other APIs — `container`, `compute`, `iam`, `iamcredentials`, `sts`,
   `artifactregistry`, `sqladmin` — are enabled automatically by Terraform via
   `google_project_service` in `gke.tf`. Wait ~2–3 min after enabling for propagation.)

3. **Create the bootstrap Terraform state bucket** (the backend in `providers.tf` — another
   chicken-egg: Terraform stores its own state here, so it must exist first):
   ```bash
   gcloud storage buckets create gs://multi-cloud-agent-bootstrap-tfstate \
     --project=<GCP_PROJECT_ID> --location=europe-west3 \
     --uniform-bucket-level-access --public-access-prevention
   gcloud storage buckets update gs://multi-cloud-agent-bootstrap-tfstate --versioning
   ```
   > Do NOT create `multi-cloud-agent-tfstate` — that pipeline-state bucket is created BY this
   > bootstrap (`storage.tf`); pre-creating it causes a 409 conflict.

4. **Create the Service Account + key** used by CI (`GCP_SA_KEY_JSON`):
   Console → IAM & Admin → Service Accounts → Create → grant **Editor** + **Project IAM Admin**
   (or the granular set: Kubernetes Engine Admin, Cloud SQL Admin, Artifact Registry Admin,
   Storage Admin, Service Account Admin, Service Usage Admin) → Keys → Add key → JSON.

---

## 2. GitHub configuration

**Secrets:** `GCP_SA_KEY_JSON` (the SA-key JSON content), `MYSQL_DB_PASSWORD`.

**Variables:** `GCP_PROJECT_ID`, `MYSQL_DB_HOST` (set *after* bootstrap — the Cloud SQL public IP),
`MYSQL_DB_PORT=3306`, `MYSQL_DB_USER=pipeline_user`, `MYSQL_DB_NAME=marketing_raw`.

The same `MYSQL_DB_PASSWORD` secret is consumed twice: as `TF_VAR_db_password` (bootstrap sets
the Cloud SQL user's password) and at runtime via `cloud_get()`.

---

## 3. Run sequence

1. **Bootstrap:** `run_agent.yml` → `bootstrap_cloud: gcp`, `pipeline: skip`, `sync_knowledge_base: skip`.
2. **Read the Cloud SQL IP** → set it as the `MYSQL_DB_HOST` variable:
   ```bash
   gcloud sql instances describe multi-cloud-agent-mysql --format="value(ipAddresses[0].ipAddress)"
   ```
3. **Pipeline:** `run_agent.yml` → `pipeline: global_marketing`, `bootstrap_cloud: skip`,
   `sync_knowledge_base: sync` (the agent reads `terraform_gcp_bucket.md` from Pinecone),
   optionally `inject_chaos: inject` (`global_marketing`, `mysql`) for dirty data.

---

## 4. Teardown

`destroy.yml` currently covers **aws** and **azure** only — a GCP path is not yet wired. Until
it is, tear down manually (or via `terraform destroy` in this dir) and remember GKE Autopilot +
Cloud SQL are the cost drivers.
