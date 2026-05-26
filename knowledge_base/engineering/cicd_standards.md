# STANDARD: GITHUB ACTIONS CI/CD PIPELINES (MULTI-CLOUD)
This standard defines the mandatory, modular structure for GitHub Actions workflows. The pipeline is split into a provider-agnostic core and cloud-specific authentication modules.

> **CRITICAL — GitHub Actions Expression Syntax:** All GitHub Actions expressions MUST use `${{ }}` with the `$` prefix — never bare `{{ }}`. Writing `{{ github.sha }}` instead of `${{ github.sha }}` is a syntax error that causes the literal string `{{ github.sha }}` to appear in the command, breaking Docker builds and all downstream steps. Every occurrence of `secrets.*`, `github.*`, `env.*`, and `matrix.*` in the generated YAML must be wrapped in `${{ }}`.

---

**## 1. WORKFLOW TRIGGER & STRUCTURE**
- **File Location:** `/.github/workflows/{{project_id}}_pipeline.yml`.
- **Triggers:** Use `on: push` with `paths` filters targeting `projects/{{project_folder}}/**`.
- **Job Name:** The single job MUST be named `deploy`.
- **Global Env:** Every job must include `env: GH_TOKEN: ${{ secrets.GH_TOKEN }}`.

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
    - `aws-region: ${{ secrets.AWS_DEFAULT_REGION }}`
- **Registry:** `aws-actions/amazon-ecr-login@v2`.
- **Kubeconfig:** `aws eks update-kubeconfig --region ${{ secrets.AWS_DEFAULT_REGION }} --name {{eks_cluster_name}}`

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
The following steps MUST appear in this exact order:

```yaml
- name: Configure AWS Credentials
  uses: aws-actions/configure-aws-credentials@v4
  with:
    aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
    aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
    aws-region: ${{ secrets.AWS_DEFAULT_REGION }}
- name: ECR Login
  uses: aws-actions/amazon-ecr-login@v2
- name: Build and Push Docker Image
  run: |
    docker build -t <CLOUD_SETUP.ecr_repository>:${{ github.sha }} -f projects/{{project_folder}}/Dockerfile projects/{{project_folder}}/
    docker push <CLOUD_SETUP.ecr_repository>:${{ github.sha }}
    docker tag <CLOUD_SETUP.ecr_repository>:${{ github.sha }} <CLOUD_SETUP.ecr_repository>:latest
    docker push <CLOUD_SETUP.ecr_repository>:latest
- name: Update Kubeconfig
  run: aws eks update-kubeconfig --region ${{ secrets.AWS_DEFAULT_REGION }} --name <CLOUD_SETUP.eks_cluster_name>
- name: Set Image Tag in Job Manifest
  run: |
    sed -i 's|image: <CLOUD_SETUP.ecr_repository>.*|image: <CLOUD_SETUP.ecr_repository>:${{ github.sha }}|' projects/{{project_folder}}/k8s/job.yaml
- name: Deploy Shared Services to Kubernetes
  run: |
    kubectl apply -f projects/{{project_folder}}/k8s/00_namespaces.yaml
    kubectl apply -f projects/{{project_folder}}/k8s/configmaps.yaml
    kubectl apply -f projects/{{project_folder}}/k8s/prometheus_deployment.yaml
    kubectl apply -f projects/{{project_folder}}/k8s/trino_deployment.yaml
    kubectl apply -f projects/{{project_folder}}/k8s/grafana_deployment.yaml
    kubectl rollout restart deployment/trino -n analytics
    kubectl rollout status deployment/trino -n analytics --timeout=120s
- name: Create DB Credentials Secret
  run: |
    # Secret keys must match the env var names the pipeline script reads.
    # Use the correct secret names per cloud provider:
    #   AWS  (eu_sales)         → POSTGRES_DB_* secrets
    #   Azure (us_crm)          → CRM_DB_* secrets
    #   GCP  (global_marketing) → MYSQL_DB_* secrets
    kubectl create secret generic <PROJECT_ID>-db-credentials \
      -n analytics \
      --from-literal=<DB_HOST_KEY>=${{ secrets.<DB_HOST_SECRET> }} \
      --from-literal=<DB_PORT_KEY>=${{ secrets.<DB_PORT_SECRET> }} \
      --from-literal=<DB_USER_KEY>=${{ secrets.<DB_USER_SECRET> }} \
      --from-literal=<DB_PASSWORD_KEY>=${{ secrets.<DB_PASSWORD_SECRET> }} \
      --from-literal=<DB_NAME_KEY>=${{ secrets.<DB_NAME_SECRET> }} \
      --dry-run=client -o yaml | kubectl apply -f -
- name: Deploy Pipeline Job to Kubernetes
  run: |
    kubectl delete job -l component=pipeline-job -n analytics --ignore-not-found=true
    kubectl apply -f projects/{{project_folder}}/k8s/job.yaml
- name: Check Deployment Status
  run: |
    kubectl get pods -n analytics
    kubectl get pods -n monitoring
    for i in $(seq 1 60); do
      SUCCEEDED=$(kubectl get job -l component=pipeline-job -n analytics -o jsonpath='{.items[0].status.succeeded}' 2>/dev/null)
      FAILED=$(kubectl get job -l component=pipeline-job -n analytics -o jsonpath='{.items[0].status.failed}' 2>/dev/null)
      if [ "$SUCCEEDED" = "1" ]; then
        echo "Job completed successfully"; exit 0
      fi
      if [ -n "$FAILED" ] && [ "$FAILED" -gt 0 ]; then
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

Replace all `<...>` placeholders with the literal values from the infrastructure context. Use the following per-cloud mappings:

| Cloud | DB secret prefix | `--from-literal` keys |
|---|---|---|
| AWS (`eu_sales`) | `POSTGRES_DB_` | `POSTGRES_DB_HOST`, `POSTGRES_DB_PORT`, `POSTGRES_DB_USER`, `POSTGRES_DB_PASSWORD`, `POSTGRES_DB_NAME` |
| Azure (`us_crm`) | `CRM_DB_` | `CRM_DB_HOST`, `CRM_DB_PORT`, `CRM_DB_USER`, `CRM_DB_PASSWORD`, `CRM_DB_NAME` |
| GCP (`global_marketing`) | `MYSQL_DB_` | `MYSQL_DB_HOST`, `MYSQL_DB_PORT`, `MYSQL_DB_USER`, `MYSQL_DB_PASSWORD`, `MYSQL_DB_NAME` |

The secret key names must match exactly what the Python pipeline script reads via `os.getenv()`.

---

**## 5. SECURITY & COMPLIANCE**
- **Secret Usage:** All credentials must be sourced from GitHub Secrets.
- **Isolation:** Pipelines must be restricted to their respective project namespaces to prevent cross-project interference.