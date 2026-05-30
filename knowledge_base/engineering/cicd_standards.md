# STANDARD: GITHUB ACTIONS CI/CD PIPELINES (MULTI-CLOUD)
This standard defines the mandatory, modular structure for GitHub Actions workflows. The pipeline is split into a provider-agnostic core and cloud-specific authentication modules.

> **CRITICAL — GitHub Actions Expression Syntax:** All GitHub Actions expressions MUST use `${{ }}` with the `$` prefix — never bare `{{ }}`. Writing `{{ github.sha }}` instead of `${{ github.sha }}` is a syntax error that causes the literal string `{{ github.sha }}` to appear in the command, breaking Docker builds and all downstream steps. Every occurrence of `secrets.*`, `github.*`, `env.*`, and `matrix.*` in the generated YAML must be wrapped in `${{ }}`.

---

**## 1. WORKFLOW TRIGGER & STRUCTURE**
- **File Location:** `/.github/workflows/{{project_id}}_pipeline.yml`.
- **Triggers:** This is a **standalone repository** — use `on: push: paths: ['**']` or omit `paths` entirely. Never use `projects/{{project_folder}}/**` or any `projects/...` prefix — this is not a monorepo.
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
    - `aws-region: ${{ vars.AWS_DEFAULT_REGION }}`
- **Registry:** `aws-actions/amazon-ecr-login@v2`.
- **Kubeconfig:** `aws eks update-kubeconfig --region ${{ vars.AWS_DEFAULT_REGION }} --name {{eks_cluster_name}}`

### 3.2 Module: GCP (target_cloud: gcp)
- **Auth:** Use `google-github-actions/auth@v2` (via Workload Identity Federation or Service Account JSON secrets).
- **Registry:** `google-github-actions/setup-gcloud@v2` to configure Docker for Artifact Registry/GCR.
- **Kubeconfig:** `gcloud container clusters get-credentials {{gke_cluster_name}} --region {{region}} --project {{gcp_project_id}}`

### 3.3 Module: Azure (target_cloud: azure)
- **Auth:** Use `azure/login@v2` with `creds: ${{ secrets.AZURE_CREDENTIALS }}`.
- **Registry:** `azure/docker-login@v1` with `login-server: {{acr_name}}.azurecr.io`.
- **Kubeconfig:** `az aks get-credentials --resource-group {{resource_group}} --name {{aks_cluster_name}}`

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
    kubectl rollout restart deployment/trino -n analytics
    kubectl rollout status deployment/trino -n analytics --timeout=120s
- name: Create DB Credentials Secret
  run: |
    # Secret name must be RFC 1123 and match job.yaml envFrom.secretRef.name exactly.
    # AWS: cloud_get() reads credentials from SSM Parameter Store via IRSA — the secret
    # is created empty so the pod starts (envFrom: secretRef requires the object to exist).
    # SSM is the single source of truth; env vars are never needed if bootstrap has run.
    # GCP/Azure: populate with actual values (MYSQL_DB_* / CRM_DB_*) from GitHub vars/secrets.
    kubectl create secret generic <project_id_rfc1123>-db-credentials \
      -n analytics \
      --dry-run=client -o yaml | kubectl apply -f -
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
| AWS | `POSTGRES_DB_HOST`, `POSTGRES_DB_PORT`, `POSTGRES_DB_USER`, `POSTGRES_DB_PASSWORD`, `POSTGRES_DB_NAME` |
| Azure | `CRM_DB_HOST`, `CRM_DB_PORT`, `CRM_DB_USER`, `CRM_DB_PASSWORD`, `CRM_DB_NAME` |
| GCP | `MYSQL_DB_HOST`, `MYSQL_DB_PORT`, `MYSQL_DB_USER`, `MYSQL_DB_PASSWORD`, `MYSQL_DB_NAME` |

---

**## 5. SECURITY & COMPLIANCE**
- **Secret Usage:** DB credentials are read at runtime by `cloud_get()` — AWS reads from SSM Parameter Store via IRSA (K8s secret exists but is empty), GCP/Azure read from env vars injected via K8s secret. Never hardcode credentials in workflow files.
- **Isolation:** Pipelines must be restricted to their respective project namespaces to prevent cross-project interference.