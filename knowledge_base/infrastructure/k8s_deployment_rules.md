# STANDARD: KUBERNETES DEPLOYMENT & ORCHESTRATION
This standard defines the mandatory structure and security protocols for deploying the Data Fabric stack (Trino, Grafana, and Data Jobs).

**VALIDATION REQUIREMENT:** After generating each manifest file, you MUST call `validate_generated_code` on it immediately. If validation returns errors, fix them before proceeding to the next file. An unvalidated manifest MUST NOT be considered complete.

---

## 1. MANDATORY MANIFEST STRUCTURE
All projects must generate and apply the following files in the `/k8s` directory:

- **00_namespaces.yaml:** Defines EXACTLY two namespaces (`analytics` and `monitoring`) AND the IRSA ServiceAccount. See Section 8 for the mandatory ServiceAccount spec.
- **trino_deployment.yaml:** Deployment + ClusterIP Service (name: `trino`) in the `analytics` namespace.
- **grafana_deployment.yaml:** Deployment + LoadBalancer Service in the `monitoring` namespace.
- **prometheus_deployment.yaml:** Prometheus + Pushgateway — both Deployments and ClusterIP Services in the `monitoring` namespace.
- **configmaps.yaml:** Five ConfigMaps in a single file separated by `---`. Each must be in the namespace of the pod that mounts it: `trino-sql-config` in `analytics` (SQL scripts), `hive-catalog-config` in `analytics` (Trino Hive/Glue catalog configuration), `grafana-dash-config` in `monitoring` (dashboard JSON), `grafana-datasource-config` in `monitoring` (Prometheus datasource), `prometheus-config` in `monitoring` (scrape config). Content must be the ACTUAL file content from the generated artifacts — never placeholders.
- **job.yaml:** The main execution unit for the data pipeline. Namespace: `analytics`.

---

## 2. THE PIPELINE JOB SPECIFICATION

`job.yaml` MUST be in the `analytics` namespace. It follows a multi-container pattern with an initContainer for schema setup and a main container for data processing.

**CRITICAL rules:**
- Namespace: `analytics`
- Labels: `project_id: <project_id>` and `component: pipeline-job` on all resources
- `restartPolicy: Never`
- Resources: minimum `1Gi` memory, `500m` CPU for the main container
- `serviceAccountName`: use the IRSA service account from infrastructure context — never skip this
- Database credentials via `envFrom: secretRef`

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: <project_id>-job
  namespace: analytics
  labels:
    project_id: <project_id>
    component: pipeline-job
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        project_id: <project_id>
        component: pipeline-job
    spec:
      serviceAccountName: <irsa_service_account>
      restartPolicy: Never
      initContainers:
      - name: init-trino
        image: trinodb/trino:403
        command:
        - /bin/sh
        - -c
        - |
          until trino --server http://trino.analytics.svc.cluster.local:8080 --execute "SELECT 1" > /dev/null 2>&1; do
            echo "Waiting for Trino to be ready..."; sleep 5;
          done
          trino --server http://trino.analytics.svc.cluster.local:8080 --file /scripts/setup_trino.sql
        volumeMounts:
        - name: sql-scripts
          mountPath: /scripts
      containers:
      - name: pipeline
        image: <ecr_image_uri>
        env:
        - name: PROJECT_ID
          value: "<project_id>"
        - name: CLOUD_PROVIDER
          value: "<cloud_provider>"         # aws | azure | gcp — required for Prometheus labels
        - name: BUCKET_NAME
          value: "<bucket_name>"
        - name: TRINO_HOST
          value: "trino.analytics.svc.cluster.local"
        - name: PUSHGATEWAY_URL
          value: "http://pushgateway.monitoring.svc.cluster.local:9091"
        envFrom:
        - secretRef:
            name: <project_id>-db-credentials
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
        volumeMounts:
        - name: sql-scripts
          mountPath: /scripts
      volumes:
      - name: sql-scripts
        configMap:
          name: trino-sql-config
```

### configmaps.yaml — MANDATORY STRUCTURE
Five ConfigMaps in a single file separated by `---`. Content must be the ACTUAL generated file content — never placeholders like `-- SQL setup commands here`.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: trino-sql-config
  namespace: analytics
  labels:
    project_id: <project_id>
data:
  setup_trino.sql: |
    <ACTUAL CONTENT OF sql/setup_trino.sql>
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: hive-catalog-config
  namespace: analytics
  labels:
    project_id: <project_id>
data:
  hive.properties: |
    connector.name=hive
    hive.metastore=glue
    hive.metastore.glue.region=<aws_region>
    hive.s3.region=<aws_region>
    hive.s3.path-style-access=true
    hive.allow-drop-table=true
    hive.allow-rename-table=true
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dash-config
  namespace: monitoring
  labels:
    project_id: <project_id>
data:
  monitoring_specs.json: |
    <ACTUAL CONTENT OF dashboards/monitoring_specs.json>
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasource-config
  namespace: monitoring
  labels:
    project_id: <project_id>
data:
  datasource.yaml: |
    apiVersion: 1
    datasources:
      - name: Prometheus
        type: prometheus
        access: proxy
        url: http://prometheus.monitoring.svc.cluster.local:9090
        isDefault: true
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: monitoring
  labels:
    project_id: <project_id>
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
    scrape_configs:
      - job_name: pushgateway
        static_configs:
          - targets: ['pushgateway.monitoring.svc.cluster.local:9091']
        honor_labels: true
```

--- 

## 3. SHARED SERVICES (TRINO & GRAFANA)

### 3.1 trino_deployment.yaml — MANDATORY STRUCTURE
`trino_deployment.yaml` MUST contain both a `Deployment` AND a `Service` in a single file, separated by `---`.

**CRITICAL rules:**
- Namespace: `analytics` (never invent another name)
- Image: `trinodb/trino:403` (never use `trino:latest`)
- Resources: minimum `2Gi` memory, `1000m` CPU
- Labels: every resource must include `project_id: <project_id>` and `component: trino`
- Service type: `ClusterIP`, port `8080`, name `trino` — this name is used by Jobs to reach Trino at `trino.analytics.svc.cluster.local:8080`
- `serviceAccountName`: must be set to the IRSA service account so Trino can call AWS Glue and S3
- Catalog volume: mount `hive-catalog-config` at `/etc/trino/catalog` so Trino picks up the Hive/Glue connector on startup
- ConfigMap volume: mount `trino-sql-config` at `/scripts`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: trino
  namespace: analytics
  labels:
    project_id: <project_id>
    component: trino
spec:
  replicas: 1
  progressDeadlineSeconds: 1800
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: trino
  template:
    metadata:
      labels:
        app: trino
        project_id: <project_id>
        component: trino
    spec:
      serviceAccountName: <k8s_service_account_name>
      containers:
      - name: trino
        image: trinodb/trino:403
        ports:
        - containerPort: 8080
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
        volumeMounts:
        - name: hive-catalog
          mountPath: /etc/trino/catalog
        - name: sql-scripts
          mountPath: /scripts
      volumes:
      - name: hive-catalog
        configMap:
          name: hive-catalog-config
      - name: sql-scripts
        configMap:
          name: trino-sql-config
---
apiVersion: v1
kind: Service
metadata:
  name: trino
  namespace: analytics
  labels:
    project_id: <project_id>
    component: trino
spec:
  selector:
    app: trino
  ports:
  - port: 8080
    targetPort: 8080
  type: ClusterIP
```

### 3.2 grafana_deployment.yaml — MANDATORY STRUCTURE
`grafana_deployment.yaml` MUST contain both a `Deployment` AND a `Service` in a single file, separated by `---`.

**CRITICAL rules:**
- Namespace: `monitoring` (never invent another name)
- Image: `grafana/grafana:10.4.2` (never use `grafana/grafana:latest`)
- Resources: minimum `512Mi` memory, `250m` CPU
- Labels: every resource must include `project_id: <project_id>` and `component: grafana`
- Service type: `LoadBalancer`, port `3000`
- Mount `grafana-dash-config` at `/etc/grafana/provisioning/dashboards` (dashboard JSON)
- Mount `grafana-datasource-config` at `/etc/grafana/provisioning/datasources` (Prometheus auto-wiring)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana
  namespace: monitoring
  labels:
    project_id: <project_id>
    component: grafana
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grafana
  template:
    metadata:
      labels:
        app: grafana
        project_id: <project_id>
        component: grafana
    spec:
      containers:
      - name: grafana
        image: grafana/grafana:10.4.2
        ports:
        - containerPort: 3000
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        volumeMounts:
        - name: dashboards
          mountPath: /etc/grafana/provisioning/dashboards
        - name: datasources
          mountPath: /etc/grafana/provisioning/datasources
      volumes:
      - name: dashboards
        configMap:
          name: grafana-dash-config
      - name: datasources
        configMap:
          name: grafana-datasource-config
---
apiVersion: v1
kind: Service
metadata:
  name: grafana
  namespace: monitoring
  labels:
    project_id: <project_id>
    component: grafana
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing
spec:
  selector:
    app: grafana
  ports:
  - port: 3000
    targetPort: 3000
  type: LoadBalancer
```

> **Why the annotation is required:** Without `aws-load-balancer-scheme: internet-facing`, the AWS Load Balancer Controller defaults to an *internal* load balancer and looks for subnets tagged `kubernetes.io/role/internal-elb`. Default VPC subnets are public and carry `kubernetes.io/role/elb` instead, causing `FailedBuildModel` and a permanently `<pending>` EXTERNAL-IP. The annotation forces an internet-facing NLB that resolves against the correct subnet tag.

---

### 3.3 prometheus_deployment.yaml — MANDATORY STRUCTURE
`prometheus_deployment.yaml` MUST contain four resources in a single file: Prometheus Deployment, Prometheus Service, Pushgateway Deployment, Pushgateway Service — all in the `monitoring` namespace, separated by `---`.

**CRITICAL rules:**
- Prometheus image: `prom/prometheus:v2.51.0` (never `latest`)
- Pushgateway image: `prom/pushgateway:v1.8.0` (never `latest`)
- Prometheus mounts `prometheus-config` ConfigMap at `/etc/prometheus`
- Both services are `ClusterIP` — Prometheus and Pushgateway are internal only (Grafana and the pipeline Job reach them via cluster DNS)
- Prometheus Service name must be `prometheus` (cluster DNS: `prometheus.monitoring.svc.cluster.local:9090`)
- Pushgateway Service name must be `pushgateway` (cluster DNS: `pushgateway.monitoring.svc.cluster.local:9091`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
  namespace: monitoring
  labels:
    project_id: <project_id>
    component: prometheus
spec:
  replicas: 1
  selector:
    matchLabels:
      app: prometheus
  template:
    metadata:
      labels:
        app: prometheus
        project_id: <project_id>
        component: prometheus
    spec:
      containers:
      - name: prometheus
        image: prom/prometheus:v2.51.0
        args:
          - "--config.file=/etc/prometheus/prometheus.yml"
        ports:
        - containerPort: 9090
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        volumeMounts:
        - name: config
          mountPath: /etc/prometheus
      volumes:
      - name: config
        configMap:
          name: prometheus-config
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: monitoring
  labels:
    project_id: <project_id>
    component: prometheus
spec:
  selector:
    app: prometheus
  ports:
  - port: 9090
    targetPort: 9090
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pushgateway
  namespace: monitoring
  labels:
    project_id: <project_id>
    component: pushgateway
spec:
  replicas: 1
  selector:
    matchLabels:
      app: pushgateway
  template:
    metadata:
      labels:
        app: pushgateway
        project_id: <project_id>
        component: pushgateway
    spec:
      containers:
      - name: pushgateway
        image: prom/pushgateway:v1.8.0
        ports:
        - containerPort: 9091
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
          limits:
            memory: "128Mi"
            cpu: "100m"
---
apiVersion: v1
kind: Service
metadata:
  name: pushgateway
  namespace: monitoring
  labels:
    project_id: <project_id>
    component: pushgateway
spec:
  selector:
    app: pushgateway
  ports:
  - port: 9091
    targetPort: 9091
  type: ClusterIP
```

---

## 4. NAMING SANITIZATION (RFC 1123)
Kubernetes rejects resource names that contain underscores (`_`). Before using `project_id` in any `metadata.name` or `secretRef.name` field, replace every underscore with a hyphen:

- `pipe_sales_eu_to_s3` → `pipe-sales-eu-to-s3`

This applies to ALL derived names: `<project_id>-job`, `<project_id>-db-credentials`, labels, and any other field that uses the project identifier as part of a K8s resource name. The label values (`project_id: <value>`) are exempt — they are not subject to RFC 1123.

---

## 5. SECURITY & RESOURCE CONTROL

- **IRSA:** Always specify `serviceAccountName`. Never use node-level IAM roles.
- **Resources:** - Jobs: Minimum 1Gi RAM / 500m CPU.
    - Trino: Minimum 2Gi RAM (scale based on data volume).
- **Secrets:** Database credentials must be injected via `envFrom: secretRef`.

---

## 6. OBSERVABILITY
- **Labels:** Every resource must have `project_id` and `component` labels.
- **Heartbeat:** The pipeline script must log a final "Processing Complete" message for the Medic agent to track success.

---

## 7. TROUBLESHOOTING — KNOWN ERRORS

### Error: `spec.template: field is immutable` (Kubernetes Job Immutability)

**Symptom:**
```
The Job "<project_id>-job" is invalid: spec.template: Invalid value: ... field is immutable
```

**Root Cause:** Kubernetes `Job` objects are immutable once created. `kubectl apply` cannot modify `spec.template` after a Job exists. This error always occurs when the CI/CD pipeline runs `kubectl apply -f k8s/` on a directory that includes `job.yaml` and the Job already exists (e.g., on second and subsequent deployments). The image tag change injected by `sed` modifies `spec.template`, which Kubernetes refuses.

**Affected File to Rewrite:** The GitHub Actions workflow file at `.github/workflows/<project_id>_pipeline.yml`.

**Fix:** Replace the single `kubectl apply -f k8s/` step with the two-step pattern defined in `cicd_standards.md` Section 4. The corrected deploy steps are:

```yaml
- name: Deploy Shared Services to Kubernetes
  run: |
    kubectl apply -f projects/{{project_folder}}/k8s/00_namespaces.yaml
    kubectl apply -f projects/{{project_folder}}/k8s/configmaps.yaml
    kubectl apply -f projects/{{project_folder}}/k8s/trino_deployment.yaml
    kubectl apply -f projects/{{project_folder}}/k8s/grafana_deployment.yaml
- name: Deploy Pipeline Job to Kubernetes
  run: |
    kubectl delete job -l component=pipeline-job -n analytics --ignore-not-found=true
    kubectl apply -f projects/{{project_folder}}/k8s/job.yaml
```

**Why this works:** Deployments (`trino`, `grafana`) support rolling updates via `kubectl apply`. Jobs do not — they must be deleted and recreated on every run. The `--ignore-not-found=true` flag ensures the delete step does not fail on first deployment when no Job exists yet.

---

## 8. CLOUD-SPECIFIC SERVICE ACCOUNT — MANDATORY

The `00_namespaces.yaml` file MUST include the `ServiceAccount` in addition to the two namespace definitions. The annotation pattern depends on `cloud_provider` in the pipeline context.

The ServiceAccount MUST always:
- Be in the `analytics` namespace
- Use the name from infrastructure context (`<cloud>_setup.k8s_service_account_name`)

### 8.1 AWS — IRSA (IAM Roles for Service Accounts)

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: analytics
---
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <k8s_service_account_name>
  namespace: analytics
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<account_id>:role/<iam_role_name>
```

Replace `<k8s_service_account_name>` and `<iam_role_name>` from `aws_setup` in context. `<account_id>` is the 12-digit prefix from the ECR repository URL.

### 8.2 Azure — Azure Workload Identity

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: analytics
---
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <k8s_service_account_name>
  namespace: analytics
  annotations:
    azure.workload.identity/client-id: "<managed_identity_client_id>"
  labels:
    azure.workload.identity/use: "true"
```

Replace `<k8s_service_account_name>` from `azure_setup.k8s_service_account_name` and `<managed_identity_client_id>` from Terraform output `crm_managed_identity_client_id`.

The pipeline Job's pod template MUST also include the label:
```yaml
spec:
  template:
    metadata:
      labels:
        azure.workload.identity/use: "true"
```

### 8.3 GCP — GKE Workload Identity

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: analytics
---
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <k8s_service_account_name>
  namespace: analytics
  annotations:
    iam.gke.io/gcp-service-account: "<gcp_service_account_email>"
```

Replace `<k8s_service_account_name>` from `gcp_setup.k8s_service_account_name` and `<gcp_service_account_email>` from Terraform output `marketing_service_account_email` (format: `<account_id>@<project_id>.iam.gserviceaccount.com`).

### 8.4 Trino Catalog ConfigMap — Cloud-Specific

The `hive-catalog-config` ConfigMap in `configmaps.yaml` MUST be adapted per cloud:

**AWS (Glue metastore):**
```properties
connector.name=hive
hive.metastore=glue
hive.metastore.glue.region=<aws_region>
hive.s3.region=<aws_region>
hive.s3.path-style-access=true
hive.allow-drop-table=true
hive.allow-rename-table=true
```

**Azure (file metastore + ABFS):**
```properties
connector.name=hive
hive.metastore=file
hive.metastore.catalog.dir=abfss://<container>@<account>.dfs.core.windows.net/metastore/
hive.azure-adls-gen2.oauth2.client-id=<managed_identity_client_id>
hive.azure-adls-gen2.oauth2.credential=<client_secret>
hive.azure-adls-gen2.oauth2.endpoint=https://login.microsoftonline.com/<tenant_id>/oauth2/token
hive.allow-drop-table=true
hive.allow-rename-table=true
```

**GCP (file metastore + GCS):**
```properties
connector.name=hive
hive.metastore=file
hive.metastore.catalog.dir=gs://<bucket_name>/metastore/
hive.gcs.use-access-token=false
hive.allow-drop-table=true
hive.allow-rename-table=true
```