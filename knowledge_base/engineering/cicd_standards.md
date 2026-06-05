# STANDARD: GITHUB ACTIONS CI/CD PIPELINES (MULTI-CLOUD)
This standard defines the mandatory, modular structure for GitHub Actions workflows. The pipeline is split into a provider-agnostic core and cloud-specific authentication modules.

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
        - 'scripts/**'
        - 'k8s/**'
        - 'sql/**'
        - 'dashboards/**'
        - 'requirements.txt'
  ```

  Never use `paths: ['**']` (triggers on every commit, including standards/prompt edits) and never use `projects/{{project_folder}}/**` or any `projects/...` prefix — this is not a monorepo.
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
- **Registry:** `google-github-actions/setup-gcloud@v2` installs the CLI, then a SEPARATE explicit step **🔴 MANDATORY** — `run: gcloud auth configure-docker {{artifact_registry_region}}-docker.pkg.dev --quiet`. `setup-gcloud` alone does NOT wire Docker's credential helper, so `docker push` fails with `denied: Unauthenticated request ... artifactregistry.repositories.uploadArtifacts`. This is the GCP equivalent of Azure's mandatory `ACR Login` / AWS's `amazon-ecr-login` — never omit it. Use the Artifact Registry host `{{artifact_registry_region}}-docker.pkg.dev` (e.g. `europe-west3-docker.pkg.dev`), matching the image registry.
- **Kubeconfig:** `gcloud container clusters get-credentials {{gke_cluster_name}} --region {{region}} --project {{gcp_project_id}}`

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

      - name: Deploy Shared Services to Kubernetes   # 🔴 MUST run BEFORE the secret — 00_namespaces.yaml creates the `analytics` namespace
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