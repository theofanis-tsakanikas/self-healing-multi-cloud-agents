---
id: cicd-standards
applies_to: all
primary_consumer: infra-agent   # retrieved via Pinecone (query_vector_store); medic may also retrieve it
enforced_by: validate_generated_code (safety net) + agent prompts
last_reviewed: 2026-06-11
---

# STANDARD: GITHUB ACTIONS CI/CD PIPELINES (MULTI-CLOUD)
This standard defines the mandatory, modular structure for GitHub Actions workflows. The pipeline is split into a provider-agnostic core and cloud-specific authentication modules.

> **Placeholder notation in this standard:** `{{name}}` and `<name>` are BOTH context
> placeholders — substitute the concrete value from the orchestration context (CLOUD_SETUP,
> PROJECT_METADATA, the resolved `ecr_repository_url`, …) before emitting the file. Neither
> form may ever appear in generated output. They are unrelated to the GitHub Actions `${{ }}`
> expression syntax below, which IS emitted literally.

> **CRITICAL — GitHub Actions Expression Syntax:** All GitHub Actions expressions MUST use `${{ }}` with the `$` prefix — never bare `{{ }}`. Writing `{{ github.sha }}` instead of `${{ github.sha }}` is a syntax error that causes the literal string `{{ github.sha }}` to appear in the command, breaking Docker builds and all downstream steps. Every occurrence of `secrets.*`, `github.*`, `env.*`, and `matrix.*` in the generated YAML must be wrapped in `${{ }}`.

---

**## 1. WORKFLOW TRIGGER & STRUCTURE**
- **File Location:** `/.github/workflows/{{project_id}}_pipeline.yml`.
- **Triggers:** This is a **standalone repository** — but the pipeline must redeploy ONLY when a deployable artifact changes, never on every commit (standards, prompts, agent code, and docs must NOT trigger a deploy). Use this exact `paths` filter:

  ```yaml
  on:
    push:
      paths:
        - 'Dockerfile'
        - 'scripts/pipe_*.py'
        - 'k8s/**'
        - 'sql/**'
        - 'dashboards/**'
        - 'requirements.txt'

  permissions:
    contents: read
  ```

  Never use `paths: ['**']` (triggers on every commit, including standards/prompt edits) and never use `projects/{{project_folder}}/**` or any `projects/...` prefix — this is not a monorepo.
- **`scripts/pipe_*.py`, NOT `scripts/**`:** the `scripts/` directory also holds agent-side utilities (`seed_chaos.py`, `ingest_to_pinecone.py`, `export_bootstrap_outputs.py`) — a broad `scripts/**` makes any lint/maintenance touch to those redeploy the pipeline. Only the generated pipeline script(s) (`pipe_*.py`) are deployable artifacts.
- **Permissions:** the top-level `permissions: { contents: read }` block above is MANDATORY — the workflow authenticates to the clouds with their own credentials and must not receive a write-capable `GITHUB_TOKEN`.
- **Job Name:** The single job MUST be named `deploy`.
- **Global Env:** No custom `GH_TOKEN` env block needed — this workflow does not use the `gh` CLI. Git authentication is handled by AWS/GCP/Azure credentials. Do not add `GH_TOKEN: ${{ secrets.GH_TOKEN }}` to the job env.

**## STANDALONE REPOSITORY — PATH RULES (mandatory)**
All file references in the workflow are relative to the repository root — never use `projects/...` prefixes:
- Docker build context: `.` (not `projects/...`)
- Dockerfile: `-f Dockerfile` (not `-f projects/.../Dockerfile`)
- kubectl applies: `k8s/job.yaml` (not `projects/.../k8s/job.yaml`)
- sed image patch: `k8s/job.yaml` (not `projects/.../k8s/job.yaml`)

---

**## 2. COMMON PIPELINE CORE (AGNOSTIC)**
These steps must exist in every workflow regardless of the cloud provider:
1. **Checkout:** `actions/checkout@v4`.
2. **Heartbeat (Final Step):** The absolute last command must be:
   `run: echo "Deployment Complete"` (Mandatory for Medic Agent validation).

**DO NOT add a Setup Python or pip install step.** The Python code runs inside the Docker container — the GitHub Actions runner does not execute it directly.

---

**## 3. CLOUD-SPECIFIC AUTHENTICATION MODULES**
The Agent MUST select the logic block that matches the `target_cloud` identifier.

### 3.1 Module: AWS (target_cloud: aws)
- **Auth:** Use `aws-actions/configure-aws-credentials@v4` with:
    - `aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}`
    - `aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}`
    - `aws-region: ${{ vars.AWS_DEFAULT_REGION }}` — **NEVER substitute a literal region string (e.g. `eu-central-1`). Always use `${{ vars.AWS_DEFAULT_REGION }}` verbatim — the operator sets the region as a GitHub Variable.**
- **Registry:** `aws-actions/amazon-ecr-login@v2`.
- **Kubeconfig:** `aws eks update-kubeconfig --region ${{ vars.AWS_DEFAULT_REGION }} --name {{eks_cluster_name}}` — same rule: `${{ vars.AWS_DEFAULT_REGION }}`, never a literal.

### 3.2 Module: GCP (target_cloud: gcp)
- **Auth:** Use `google-github-actions/auth@v2` with `credentials_json: ${{ secrets.GCP_SA_KEY_JSON }}` — the SA-key JSON content. This is the EXACT secret name (same one the infra-agent Terraform and bootstrap use); never invent a different name (e.g. `GCP_CREDENTIALS`) or the deploy fails to authenticate to Artifact Registry / GKE.
- **Toolchain — 🔴 MANDATORY step right after auth:** `google-github-actions/setup-gcloud@v2` **with `install_components: 'gke-gcloud-auth-plugin'`**. This installs the gcloud CLI AND the GKE auth plugin. kubectl ≥1.26 authenticates to GKE **only** through this exec credential plugin, so the kubeconfig produced by `get-credentials` is useless without it — every `kubectl` command then fails `getting credentials: exec: executable gke-gcloud-auth-plugin not found`. (GCP analogue of AWS needing `aws-iam-authenticator` / Azure `kubelogin`.) Never omit the `install_components`.
  ```yaml
  - uses: google-github-actions/setup-gcloud@v2
    with:
      install_components: 'gke-gcloud-auth-plugin'
  ```
- **Registry:** a SEPARATE explicit step **🔴 MANDATORY** — `run: gcloud auth configure-docker {{artifact_registry_region}}-docker.pkg.dev --quiet`. `setup-gcloud` does NOT wire Docker's credential helper, so `docker push` otherwise fails `denied: Unauthenticated request ... artifactregistry.repositories.uploadArtifacts`. (GCP equivalent of Azure's `ACR Login` / AWS's `amazon-ecr-login`.) Use the Artifact Registry host `{{artifact_registry_region}}-docker.pkg.dev` (e.g. `europe-west3-docker.pkg.dev`).
- **Image path:** GCP Artifact Registry images are `HOST/PROJECT/REPOSITORY/IMAGE:TAG` — the full image reference (resolved in context as `ecr_repository_url`) already includes the IMAGE segment (the pipeline name), so use it verbatim as `<ecr_repository_url>:${{ github.sha }}`. Never push to just `HOST/PROJECT/REPOSITORY:TAG` (no image name) — `docker push` fails `name invalid: Missing image name`. (Unlike AWS ECR, where the repository IS the image.)
- **Kubeconfig:** `gcloud container clusters get-credentials {{gke_cluster_name}} --region {{region}} --project {{gcp_project_id}}` — requires the `gke-gcloud-auth-plugin` from the Toolchain step above.
- **Build & push — 🔴 use VERBATIM. There is NO image-tag `sed` step: `k8s/job.yaml` references `:latest`, which the build just pushed.**
  ```yaml
  - name: Build & Push Image
    run: |
      docker build -t <ecr_repository_url>:${{ github.sha }} -f Dockerfile .
      docker push <ecr_repository_url>:${{ github.sha }}
      docker tag  <ecr_repository_url>:${{ github.sha }} <ecr_repository_url>:latest
      docker push <ecr_repository_url>:latest
  ```
  **🔴 In `k8s/job.yaml` the pipeline container's `image:` is `<ecr_repository_url>:latest`** — the build just pushed that exact tag, so the Job pulls it directly. Do **NOT** add a "Set Image Tag" / `sed` step and **NEVER put `${{ github.sha }}` (or any `${{ … }}`) inside `k8s/job.yaml`**: a GitHub Actions expression is never evaluated by Kubernetes, so if a tag-rewrite `sed` fails to match (e.g. the image name carries a timestamp suffix) the literal `${{ github.sha }}` reaches the cluster and the pod dies `InvalidImageName`. `:latest` needs no rewrite and works whatever the image name is. **The image NAME is `<ecr_repository_url>` VERBATIM** (it already includes HOST/PROJECT/REPOSITORY/IMAGE) and must be byte-for-byte IDENTICAL in the build, the push and `k8s/job.yaml`; do not append a timestamp/date/build-id suffix.

### 3.3 Module: Azure (target_cloud: azure)
- **Auth:** Use `azure/login@v2` with `creds: ${{ secrets.AZURE_CREDENTIALS }}`.
- **Registry:** `azure/docker-login@v1` with `login-server: {{acr_name}}.azurecr.io`.
- **Kubeconfig:** `az aks get-credentials --resource-group {{resource_group}} --name {{aks_cluster_name}}`

### 3.4 Azure — COMPLETE ordered workflow (the authoritative Azure template)
For `cloud_provider: azure`, generate **exactly** these steps in this order. Section 4's
detailed AWS template below is NOT the Azure shape — do NOT translate it step-by-step and do
NOT drop any step. The five most-commonly-dropped steps are flagged 🔴 — every one is
mandatory. Substitute only the bracketed values from the infrastructure context.

```yaml
name: Deploy Pipeline

on:
  push:
    paths: ['Dockerfile', 'scripts/**', 'k8s/**', 'sql/**', 'dashboards/**', 'requirements.txt']

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Azure Login            # 🔴 MANDATORY — every `az` command below fails without it
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: ACR Login              # 🔴 MANDATORY — `docker push` is unauthorized without it
        # Registry name = the FIRST label of <acr_login_server> — the SAME registry the
        # `docker build/push` steps below use. It is the CONTAINER REGISTRY, NOT the storage
        # account (e.g. acr_login_server `mcselfhealagentacr.azurecr.io` → `mcselfhealagentacr`;
        # NEVER the storage account `uscrminsightsstorage`). Deriving it from <acr_login_server>
        # keeps it identical to the image registry and avoids confusing the two identifiers.
        # Retry up to 3× — `az acr login` intermittently hits a transient AAD-endpoint
        # 'Connection reset by peer' on GitHub runners; a single failure must not abort deploy.
        run: |
          REG="$(echo '<acr_login_server>' | cut -d'.' -f1)"
          for i in 1 2 3; do
            az acr login --name "$REG" && break || { echo "ACR login attempt $i failed (transient), retrying in 10s..."; sleep 10; }
          done

      - name: Build Azure Storage Connection String + inject Trino ABFS key
        run: |
          KEY=$(az storage account keys list -g <resource_group> -n <storage_account_name> --query '[0].value' -o tsv)
          echo "AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=<storage_account_name>;AccountKey=$KEY;EndpointSuffix=core.windows.net" >> "$GITHUB_ENV"
          sed -i "s|__ABFS_KEY__|$KEY|g" k8s/configmaps.yaml
          # NOTE: the ADLS `processed/` directory is created by Terraform
          # (azurerm_storage_data_lake_gen2_path), not here — see terraform_azure_blob.md §2.2.1.

      - name: Build and Push Docker Image
        run: |
          docker build -t <acr_login_server>/<project_id_rfc1123>:${{ github.sha }} -f Dockerfile .
          docker push <acr_login_server>/<project_id_rfc1123>:${{ github.sha }}
          docker tag  <acr_login_server>/<project_id_rfc1123>:${{ github.sha }} <acr_login_server>/<project_id_rfc1123>:latest
          docker push <acr_login_server>/<project_id_rfc1123>:latest

      - name: Update Kubeconfig
        run: az aks get-credentials --resource-group <resource_group> --name <aks_cluster_name>

      - name: Set Image Tag in Job Manifest
        run: |
          sed -i 's|image: <acr_login_server>/<project_id_rfc1123>:.*|image: <acr_login_server>/<project_id_rfc1123>:${{ github.sha }}|' k8s/job.yaml

      - name: Create Grafana Admin Secret   # 🔴 MUST run BEFORE grafana_deployment.yaml — the Deployment reads GF_SECURITY_ADMIN_PASSWORD from this Secret (public LB ⇒ never default admin/admin). Create-if-absent keeps the password stable across runs; the openssl fallback is fail-secure when no repo secret is set. Retrieve with: kubectl get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d
        env:
          GRAFANA_ADMIN_PASSWORD: ${{ secrets.GRAFANA_ADMIN_PASSWORD }}
        run: |
          kubectl apply -f k8s/00_namespaces.yaml   # idempotent — ensures `monitoring` exists
          kubectl get secret grafana-admin -n monitoring >/dev/null 2>&1 || \
            kubectl create secret generic grafana-admin -n monitoring \
              --from-literal=admin-password="${GRAFANA_ADMIN_PASSWORD:-$(openssl rand -base64 24)}"

      - name: Deploy Shared Services to Kubernetes   # 🔴 MUST run BEFORE the db secret — 00_namespaces.yaml creates the `analytics` namespace
        run: |
          kubectl apply -f k8s/00_namespaces.yaml
          kubectl apply -f k8s/configmaps.yaml
          kubectl apply -f k8s/prometheus_deployment.yaml   # 🔴 MANDATORY — Prometheus + Pushgateway; without it metrics push fails → Grafana "No data"
          kubectl apply -f k8s/trino_deployment.yaml
          kubectl apply -f k8s/grafana_deployment.yaml
          kubectl rollout restart deployment/trino -n analytics      # 🔴 reload ConfigMaps (the sed'd ABFS key) on re-deploy
          kubectl rollout restart deployment/grafana -n monitoring
          kubectl rollout status deployment/trino -n analytics --timeout=120s
          kubectl rollout status deployment/grafana -n monitoring --timeout=120s

      - name: Create DB Credentials Secret      # HOST/PORT/USER/NAME = vars.* (NOT secrets.*); password = secret. MUST come AFTER the namespace exists (above) or `kubectl` fails with `namespaces "analytics" not found`.
        run: |
          kubectl create secret generic <project_id_rfc1123>-db-credentials -n analytics \
            --from-literal=CRM_DB_HOST=${{ vars.CRM_DB_HOST }} \
            --from-literal=CRM_DB_PORT=${{ vars.CRM_DB_PORT }} \
            --from-literal=CRM_DB_USER=${{ vars.CRM_DB_USER }} \
            --from-literal=CRM_DB_NAME=${{ vars.CRM_DB_NAME }} \
            --from-literal=CRM_DB_PASSWORD=${{ secrets.AZURE_DB_PASSWORD }} \
            --from-literal=AZURE_STORAGE_CONNECTION_STRING="$AZURE_STORAGE_CONNECTION_STRING" \
            --dry-run=client -o yaml | kubectl apply -f -

      - name: Deploy Pipeline Job to Kubernetes
        run: |
          kubectl delete job -l component=pipeline-job -n analytics --ignore-not-found=true
          kubectl apply -f k8s/job.yaml

      - name: Check Deployment Status
        run: |
          # poll the Job; on failure/timeout dump init-trino + pipeline container logs (see AWS template)
          echo "see the status-polling block in Section 4 — identical for all clouds"
```

---

**## 4. DEPLOYMENT EXECUTION**

### Secret naming — mandatory alignment rule
The DB credentials secret name MUST be identical in both the GHA workflow and `job.yaml`. Use a **static, RFC 1123 name** (no timestamp — timestamps are only for Job names, not Secret names):

```
<project_id_rfc1123>-db-credentials
```

RFC 1123 conversion: replace every underscore with a hyphen, lowercase everything.
- `pipe_eu_sales_to_s3` → `pipe-eu-sales-to-s3-db-credentials` ✓
- `PIPE_EU_SALES_TO_S3-20260528-0505-db-credentials` ✗ (uppercase + underscore + timestamp — invalid)

The secret is created idempotently (`--dry-run=client -o yaml | kubectl apply -f -`) so running the workflow multiple times does not fail.

### ECR / Registry URL — no placeholders
The ECR repository URL MUST be the **real full URL** from the infrastructure context — never `<AWS_ACCOUNT_ID>` or `<CLOUD_SETUP.ecr_repository>`. Use the `ecr_repository_url` value from the orchestration context (captured from terraform outputs or `.bootstrap_outputs.json`):

```
123456789012.dkr.ecr.eu-central-1.amazonaws.com/eu-sales-pipeline-repo
```

The following steps MUST appear in this exact order:

```yaml
- name: Configure AWS Credentials
  uses: aws-actions/configure-aws-credentials@v4
  with:
    aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
    aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
    aws-region: ${{ vars.AWS_DEFAULT_REGION }}
- name: ECR Login
  uses: aws-actions/amazon-ecr-login@v2
- name: Build and Push Docker Image
  run: |
    docker build -t <real_ecr_url>:${{ github.sha }} -f Dockerfile .
    docker push <real_ecr_url>:${{ github.sha }}
    docker tag <real_ecr_url>:${{ github.sha }} <real_ecr_url>:latest
    docker push <real_ecr_url>:latest
- name: Update Kubeconfig
  run: aws eks update-kubeconfig --region ${{ vars.AWS_DEFAULT_REGION }} --name <eks_cluster_name>
- name: Set Image Tag in Job Manifest
  run: |
    sed -i 's|image: <real_ecr_url>.*|image: <real_ecr_url>:${{ github.sha }}|' k8s/job.yaml
- name: Create Grafana Admin Secret
  # 🔴 BEFORE grafana_deployment.yaml — the Deployment reads GF_SECURITY_ADMIN_PASSWORD
  # from this Secret (public LB ⇒ never default admin/admin). Create-if-absent keeps the
  # password stable across runs; the openssl fallback is fail-secure when no repo secret
  # is configured. Retrieve it with:
  #   kubectl get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d
  env:
    GRAFANA_ADMIN_PASSWORD: ${{ secrets.GRAFANA_ADMIN_PASSWORD }}
  run: |
    kubectl apply -f k8s/00_namespaces.yaml   # idempotent — ensures `monitoring` exists
    kubectl get secret grafana-admin -n monitoring >/dev/null 2>&1 || \
      kubectl create secret generic grafana-admin -n monitoring \
        --from-literal=admin-password="${GRAFANA_ADMIN_PASSWORD:-$(openssl rand -base64 24)}"
- name: Deploy Shared Services to Kubernetes
  run: |
    kubectl apply -f k8s/00_namespaces.yaml
    kubectl apply -f k8s/configmaps.yaml
    kubectl apply -f k8s/prometheus_deployment.yaml
    kubectl apply -f k8s/trino_deployment.yaml
    kubectl apply -f k8s/grafana_deployment.yaml
    # Restart so pods re-read updated ConfigMaps (Trino: hive catalog/SQL; Grafana: dashboard provider + JSON).
    # ConfigMaps mounted as volumes are read only at container startup — kubectl apply alone does not reload them.
    kubectl rollout restart deployment/trino -n analytics
    kubectl rollout restart deployment/grafana -n monitoring
    kubectl rollout status deployment/trino -n analytics --timeout=120s
    kubectl rollout status deployment/grafana -n monitoring --timeout=120s
- name: Create DB Credentials Secret
  run: |
    # Emit EXACTLY ONE form — the one matching PROJECT_METADATA.cloud_provider. There is NO
    # default cloud: an AWS empty secret on an Azure/GCP pipeline makes cloud_get() return
    # None → `host name "None"`. Name must be RFC 1123 and match job.yaml secretRef exactly.
    #
    # ✅ AWS — EMPTY secret (cloud_get reads creds from SSM via IRSA; object just needs to exist):
    #   kubectl create secret generic <project_id_rfc1123>-db-credentials -n analytics \
    #     --dry-run=client -o yaml | kubectl apply -f -
    #
    # ✅ Azure — POPULATED secret. MUST include CRM_DB_* (no SSM) AND the storage connection
    #   string (the pipeline's idempotency check + adlfs abfss writer both read
    #   AZURE_STORAGE_CONNECTION_STRING from the pod env — provide it here):
    #   kubectl create secret generic <project_id_rfc1123>-db-credentials -n analytics \
    #     --from-literal=CRM_DB_HOST=${{ vars.CRM_DB_HOST }} \
    #     --from-literal=CRM_DB_PORT=${{ vars.CRM_DB_PORT }} \
    #     --from-literal=CRM_DB_USER=${{ vars.CRM_DB_USER }} \
    #     --from-literal=CRM_DB_NAME=${{ vars.CRM_DB_NAME }} \
    #     --from-literal=CRM_DB_PASSWORD=${{ secrets.AZURE_DB_PASSWORD }} \
    #     --from-literal=AZURE_STORAGE_CONNECTION_STRING="$AZURE_STORAGE_CONNECTION_STRING" \
    #     --dry-run=client -o yaml | kubectl apply -f -
    #
    # ✅ GCP — POPULATED secret with the MYSQL_DB_* keys (PASSWORD from secrets, rest from vars).
    #   Source = GCP Cloud SQL MySQL; the pod reads these via cloud_get("gcp", …, db_type="mysql").
    #   NO AZURE_STORAGE_CONNECTION_STRING (GCS uses the pod's Workload Identity).
    #   kubectl create secret generic <project_id_rfc1123>-db-credentials -n analytics \
    #     --from-literal=MYSQL_DB_HOST=${{ vars.MYSQL_DB_HOST }} \
    #     --from-literal=MYSQL_DB_PORT=${{ vars.MYSQL_DB_PORT }} \
    #     --from-literal=MYSQL_DB_USER=${{ vars.MYSQL_DB_USER }} \
    #     --from-literal=MYSQL_DB_NAME=${{ vars.MYSQL_DB_NAME }} \
    #     --from-literal=MYSQL_DB_PASSWORD=${{ secrets.MYSQL_DB_PASSWORD }} \
    #     --dry-run=client -o yaml | kubectl apply -f -
    #
    # Replace the comment above with the single uncommented block for the active cloud.

# ── Azure ONLY: build the storage connection string from the account key, just before the
#    secret step above, so the pipeline pod can read/write ADLS Gen2 (Workload Identity for
#    blob is not wired; the account key is the reliable path). Skip this step for AWS/GCP.
# - name: Build Azure Storage Connection String + inject Trino ABFS key
#   run: |
#     KEY=$(az storage account keys list -g <resource_group> -n <storage_account_name> --query '[0].value' -o tsv)
#     echo "AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=<storage_account_name>;AccountKey=$KEY;EndpointSuffix=core.windows.net" >> "$GITHUB_ENV"
#     sed -i "s|__ABFS_KEY__|$KEY|g" k8s/configmaps.yaml   # real key into the Trino hive-catalog-config
- name: Deploy Pipeline Job to Kubernetes
  run: |
    kubectl delete job -l component=pipeline-job -n analytics --ignore-not-found=true
    kubectl apply -f k8s/job.yaml
- name: Check Deployment Status
  run: |
    kubectl get pods -n analytics
    kubectl get pods -n monitoring
    for i in $(seq 1 60); do
      SUCCEEDED=$(kubectl get job -l component=pipeline-job -n analytics -o jsonpath='{.items[0].status.succeeded}' 2>/dev/null)
      FAILED=$(kubectl get job -l component=pipeline-job -n analytics -o jsonpath='{.items[0].status.failed}' 2>/dev/null)
      if [ "${SUCCEEDED:-0}" = "1" ]; then
        echo "Job completed successfully"; exit 0
      fi
      if [ "${FAILED:-0}" -gt 0 ]; then
        echo "Job failed. Fetching logs..."
        echo "=== init-trino logs ==="
        kubectl logs -l component=pipeline-job -n analytics -c init-trino --tail=50 2>/dev/null || echo "(init-trino logs unavailable)"
        echo "=== pipeline logs ==="
        kubectl logs -l component=pipeline-job -n analytics -c pipeline --tail=100 2>/dev/null || echo "(pipeline logs unavailable)"
        exit 1
      fi
      echo "Waiting for job... ($i/60)"; sleep 10
    done
    echo "Timeout waiting for job"
    echo "=== init-trino logs ==="
    kubectl logs -l component=pipeline-job -n analytics -c init-trino --tail=50 2>/dev/null || echo "(init-trino logs unavailable)"
    echo "=== pipeline logs ==="
    kubectl logs -l component=pipeline-job -n analytics -c pipeline --tail=100 2>/dev/null || echo "(pipeline logs unavailable)"
    exit 1
```

Per-cloud `--from-literal` key mapping:

| Cloud | Secret keys |
|---|---|
| AWS | `POSTGRES_DB_HOST`, `POSTGRES_DB_PORT`, `POSTGRES_DB_USER`, `POSTGRES_DB_PASSWORD`, `POSTGRES_DB_NAME` (or empty — SSM) |
| Azure | `CRM_DB_HOST`, `CRM_DB_PORT`, `CRM_DB_USER`, `CRM_DB_PASSWORD`, `CRM_DB_NAME`, **`AZURE_STORAGE_CONNECTION_STRING`** (for the idempotency check + adlfs abfss writes) |
| GCP | `MYSQL_DB_HOST`, `MYSQL_DB_PORT`, `MYSQL_DB_USER`, `MYSQL_DB_PASSWORD`, `MYSQL_DB_NAME` |

---

**## 5. SECURITY & COMPLIANCE**
- **Secret Usage:** DB credentials are read at runtime by `cloud_get()` — AWS reads from SSM Parameter Store via IRSA (K8s secret exists but is empty), GCP/Azure read from env vars injected via K8s secret. Never hardcode credentials in workflow files.
- **Isolation:** Pipelines must be restricted to their respective project namespaces to prevent cross-project interference.
- **Grafana admin:** the deploy workflow MUST create the `grafana-admin` Secret (see "Create Grafana Admin Secret" above) before applying `grafana_deployment.yaml` — Grafana sits on a public LoadBalancer and must never come up with the default `admin/admin`.
- **Workflow token scope:** every generated workflow declares least-privilege `permissions:` at the top level — `permissions: { contents: read }` (deploys authenticate to the clouds via their own credentials, never via `GITHUB_TOKEN`).
- **Action pinning:** third-party actions are pinned to a release tag (e.g. `databricks/setup-cli@v1.2.1`) — NEVER a mutable branch like `@main`.

---

### 3.5 Module: Databricks (provider: databricks) — COMPLETE ordered workflow

A Databricks deploy is **not** docker/kubectl. NO docker build, NO `kubectl`, NO ECR/AR, NO
image tag. The artifacts are a PySpark script (`scripts/<pipeline_id>.py`) + Unity Catalog DDL
(`sql/setup_unity_catalog.sql`). **Terraform (the secret scope + `databricks_job`) is applied by
the agent's `execute_terraform`, exactly like the other clouds — NOT in this workflow.** This
deploy workflow does only: upload the script to DBFS → trigger the job → wait. Auth:
`DATABRICKS_HOST` + `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` (the **service principal**,
oauth-m2m) for the CLI — the same identity the job runs as — **plus AWS credentials** so
`terraform init` can reach the **S3 state backend** (the job id is read from `terraform output`
against the state the agent's apply already wrote). Without the AWS creds, `terraform init`
fails ("Failed to get existing workspaces / NoCredentialProviders").

```yaml
name: Deploy Pipeline
on:
  push:
    # scripts/pipe_*.py — NOT scripts/** (scripts/ also holds agent-side utilities;
    # a lint touch to those must not redeploy — same rule as Section 1).
    paths: ['scripts/pipe_*.py', 'sql/**', 'terraform/**']

permissions:
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    env:
      DATABRICKS_HOST:          ${{ secrets.DATABRICKS_HOST }}
      # The databricks CLI authenticates as the SERVICE PRINCIPAL (oauth-m2m) — the same identity
      # the job runs as. No PAT needed.
      DATABRICKS_CLIENT_ID:     ${{ secrets.DATABRICKS_CLIENT_ID }}
      DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}
      # AWS creds so `terraform init` can read the S3-backed state (job_id from `terraform output`).
      AWS_ACCESS_KEY_ID:     ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
      AWS_DEFAULT_REGION:    ${{ vars.AWS_DEFAULT_REGION }}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_wrapper: false
      - uses: databricks/setup-cli@v1.2.1        # the unified `databricks` CLI — pinned tag, never @main

      - name: Upload Spark script to DBFS
        run: |
          databricks fs mkdirs "dbfs:/pipelines/<pipeline_id>" || true
          databricks fs cp scripts/<pipeline_id>.py "dbfs:/pipelines/<pipeline_id>/<pipeline_id>.py" --overwrite

      - name: Trigger job run and wait
        working-directory: terraform
        run: |
          terraform init -input=false
          JOB_ID=$(terraform output -raw job_id)
          RUN_ID=$(databricks jobs run-now "$JOB_ID" -o json | jq -r '.run_id')
          echo "Triggered run $RUN_ID for job $JOB_ID"
          for i in $(seq 1 80); do
            RUN=$(databricks jobs get-run "$RUN_ID" -o json)
            STATE=$(echo "$RUN" | jq -r '.state.life_cycle_state')
            RESULT=$(echo "$RUN" | jq -r '.state.result_state // empty')
            echo "run $RUN_ID: $STATE $RESULT ($i/80)"
            if [ "$STATE" = "TERMINATED" ]; then
              [ "$RESULT" = "SUCCESS" ] && { echo "Job succeeded"; exit 0; }
              echo "Job failed: $RESULT"; echo "$RUN" | jq '.tasks[].state'; exit 1
            fi
            [ "$STATE" = "INTERNAL_ERROR" ] && { echo "Internal error"; exit 1; }
            sleep 15
          done
          echo "Timeout waiting for run"; exit 1

      - run: echo "Deployment Complete"
```

**Notes:**
- `DATABRICKS_HOST` + `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` are repo **Secrets**
  (workspace URL + the service principal) — used by both this workflow's CLI and the agent's
  terraform provider (oauth-m2m). The pipeline runs entirely as the SP; **no user PAT is needed**.
- The secret scope + job are created by the **agent's `execute_terraform`** (run via
  `run_agent.yml`). The terraform reads the source DB connection (host/name/user/password) from
  **SSM** via `data "aws_ssm_parameter"` (`/multi-cloud-self-healing-agent/aws/lakehouse_db_*`,
  published by the Databricks bootstrap) — there are **NO** `db_*` terraform variables, **NO**
  `terraform.tfvars` DB entries, and **NO** `POSTGRES_DB_PASSWORD` secret. Only the AWS creds
  (which the agent already has) are needed to read SSM.
- The Unity Catalog tables are created by the Spark job's `saveAsTable` on first run; the
  `sql/setup_unity_catalog.sql` artifact is the explicit-schema reference (apply it via the SQL
  Warehouse only if you want the schema pre-created).
- The job resolves its cluster by name (`data "databricks_cluster"`) and attaches the JDBC
  driver as a Maven library — see `terraform_databricks.md`. Do NOT `kubectl`/`docker` here.
