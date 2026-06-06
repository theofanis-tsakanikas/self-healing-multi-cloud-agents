# STANDARD: KUBERNETES DEPLOYMENT & ORCHESTRATION
This standard defines the mandatory structure and security protocols for deploying the Data Fabric stack (Trino, Grafana, and Data Jobs).

**VALIDATION REQUIREMENT:** After generating each manifest file, you MUST call `validate_generated_code` on it immediately. If validation returns errors, fix them before proceeding to the next file. An unvalidated manifest MUST NOT be considered complete.

---

## 0. PRE-GENERATION CHECKLIST — verify before submitting any K8s file

Before marking a file complete, confirm every item below. Skipping even one causes runtime failure:

| File | Must contain | Common omissions that break deployments |
|---|---|---|
| `trino_deployment.yaml` | Deployment **+** ClusterIP Service (2 objects, separated by `---`) | Missing Service → `trino.analytics.svc.cluster.local` never resolves |
| `grafana_deployment.yaml` | Deployment **+** `type: LoadBalancer` Service (2 objects); exposure annotation is cloud-specific — AWS only, omit on Azure/GCP | Missing Service → no external IP |
| `prometheus_deployment.yaml` | Prometheus Deployment + Service **+** Pushgateway Deployment + Service (4 objects) | Missing Pushgateway → pipeline metrics push fails at runtime |
| `configmaps.yaml` | All 5 named ConfigMaps with `labels: project_id:` on every one | Wrong key name in hive-catalog (`catalog.properties` → must be `hive.properties`) |
| `job.yaml` | `initContainers` (init-trino) **+** `containers` (pipeline) — two separate sections | Using `containers` for init-trino → it runs in parallel with pipeline, not before |

**job.yaml non-negotiables (all must be present):**
- `restartPolicy: Never` — not `OnFailure`
- `serviceAccountName` — never omit; required for S3/GCS/ADLS access via workload identity
- `backoffLimit: 0`
- All resource names are RFC 1123: **lowercase + hyphens only** — `pipe-eu-sales-to-s3-job`, not `PIPE_EU_SALES_TO_S3-job`
- `secretRef.name` is also RFC 1123: `pipe-eu-sales-to-s3-<timestamp>-db-credentials` (lowercase, hyphens)

---

## 1. MANDATORY MANIFEST STRUCTURE
All projects must generate and apply the following files in the `/k8s` directory:

- **00_namespaces.yaml:** Defines EXACTLY two namespaces (`analytics` and `monitoring`) AND the cloud-specific ServiceAccount. See Section 8 for the mandatory ServiceAccount spec.
- **trino_deployment.yaml:** Deployment + ClusterIP Service (name: `trino`) in the `analytics` namespace. **2 objects.**
- **grafana_deployment.yaml:** Deployment + LoadBalancer Service in the `monitoring` namespace. **2 objects.**
- **prometheus_deployment.yaml:** Prometheus Deployment + Service + Pushgateway Deployment + Service — all in `monitoring`. **4 objects.**
- **configmaps.yaml:** Five ConfigMaps in a single file separated by `---`. Each must be in the namespace of the pod that mounts it: `trino-sql-config` in `analytics` (SQL scripts), `hive-catalog-config` in `analytics` (Trino Hive/Glue catalog configuration), `grafana-dash-config` in `monitoring` (dashboard JSON), `grafana-datasource-config` in `monitoring` (Prometheus datasource), `prometheus-config` in `monitoring` (scrape config). Write `hive.properties`, `prometheus.yml`, `datasource.yaml` and `dashboard-provider.yaml` in full. For the two LARGE artifacts — `trino-sql-config`'s `setup_trino.sql` and `grafana-dash-config`'s `monitoring_specs.json` — output ONLY the one-line tokens `__EMBED_SETUP_TRINO_SQL__` and `__EMBED_MONITORING_SPECS_JSON__` as the block-scalar value; the deploy tool injects the real, validated file verbatim. NEVER re-type those two — re-typing the ~150-line dashboard JSON blows the output budget and truncates the later ConfigMaps.
- **job.yaml:** The main execution unit for the data pipeline. Namespace: `analytics`. Uses `initContainers` + `containers` — two distinct sections.

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
  name: <project_id_rfc1123>-job   # RFC 1123: lowercase + hyphens — e.g. pipe-eu-sales-to-s3-job
  namespace: analytics
  labels:
    project_id: <project_id>        # label values are exempt from RFC 1123
    component: pipeline-job
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        project_id: <project_id>
        component: pipeline-job
    spec:
      serviceAccountName: <k8s_service_account_name>   # MANDATORY — never omit
      restartPolicy: Never                              # MANDATORY — never OnFailure
      initContainers:                                   # MANDATORY — runs BEFORE containers
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
      containers:                                       # MANDATORY — the actual pipeline script
      - name: pipeline
        image: <ecr_image_uri>                          # full ECR URL — never a placeholder
        env:
        - name: PROJECT_ID
          value: "<project_id>"                         # the PIPELINE id (e.g. pipe_mkt_global_to_gcp) — same as every project_id label / metric label. On GCP NEVER the cloud project (gcp_project_id); that is terraform-only.
        - name: CLOUD_PROVIDER
          value: "<cloud_provider>"                     # aws | azure | gcp
        - name: DESTINATION_URI
          value: "<LOGICAL_DESTINATION.uri>"            # e.g. s3://eu-sales-insights-data/processed/ — the pipeline script reads os.getenv("DESTINATION_URI") at runtime; omitting this causes immediate failure with None
        - name: BUCKET_NAME
          value: "<bucket_name>"
        - name: TRINO_HOST
          value: "trino.analytics.svc.cluster.local"   # hostname ONLY — no :port
        - name: PUSHGATEWAY_URL
          value: "http://pushgateway.monitoring.svc.cluster.local:9091"  # http:// scheme is MANDATORY — push_to_gateway() requires a full URL
        envFrom:
        - secretRef:
            name: <project_id_rfc1123>-db-credentials  # RFC 1123: pipe-eu-sales-to-s3-<timestamp>-db-credentials
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

### configmaps.yaml — 5 OBJECTS, all in one file separated by `---`
Content must be the ACTUAL generated file content — never placeholders like `-- SQL setup commands here`.

**hive-catalog-config key name is always `hive.properties` (never `catalog.properties` or `hive.properties.yaml`).** Content is cloud-specific — use Section 8.4 for the active cloud. The AWS template below is the default; for Azure or GCP substitute Section 8.4 content verbatim.

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
    __EMBED_SETUP_TRINO_SQL__
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
  dashboard-provider.yaml: |
    apiVersion: 1
    providers:
    - name: default
      orgId: 1
      folder: ''
      type: file
      disableDeletion: false
      editable: true
      options:
        path: /etc/grafana/provisioning/dashboards
  monitoring_specs.json: |
    __EMBED_MONITORING_SPECS_JSON__
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

### 3.1 trino_deployment.yaml — 2 OBJECTS: Deployment + ClusterIP Service
`trino_deployment.yaml` MUST contain both a `Deployment` AND a `Service` in a single file, separated by `---`. A Deployment without a Service means `trino.analytics.svc.cluster.local` never resolves and every job init-container fails.

**CRITICAL rules:**
- Namespace: `analytics` (never invent another name)
- Image: `trinodb/trino:403` (never use `trino:latest`)
- Resources: minimum `2Gi` memory, `1000m` CPU
- Labels: every resource must include `project_id: <project_id>` and `component: trino`
- Service type: `ClusterIP`, port `8080`, name `trino` — this name is used by Jobs to reach Trino at `trino.analytics.svc.cluster.local:8080`
- `serviceAccountName`: must be set to the cloud service account so Trino can call Glue/GCS/ADLS and S3/GCS/ADLS
- Catalog volume: mount `hive-catalog-config` at `/etc/trino/catalog` so Trino picks up the Hive connector on startup
- ConfigMap volume: mount `trino-sql-config` at `/scripts`
- **DO NOT add `TRINO_HOST` or `PUSHGATEWAY_URL` env vars to the Trino container** — these belong on the pipeline Job container only

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

### 3.2 grafana_deployment.yaml — 2 OBJECTS: Deployment + LoadBalancer Service
`grafana_deployment.yaml` MUST contain both a `Deployment` AND a `Service` in a single file, separated by `---`. Without the `type: LoadBalancer` Service, Grafana is permanently `<pending>` with no external IP. The exposure annotation is **cloud-specific** — apply only the one matching `cloud_provider`:
- **AWS:** `service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing`
- **Azure (AKS) & GCP (GKE):** **NO annotation** — a `type: LoadBalancer` Service gets a public IP by default. Do NOT copy the AWS annotation onto AKS/GKE; it is silently ignored (works but is wrong).

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
        env:
        # Lock the UI to English so dateTimeFromNow renders "X minutes ago", not a
        # browser-locale translation (e.g. Greek "λίγα δευτερόλεπτα πριν").
        - name: GF_USERS_DEFAULT_LANGUAGE
          value: "en-US"
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
  # 🔴 The annotations block below is AWS-ONLY. Emit it ONLY when cloud_provider == "aws".
  # For cloud_provider "azure" (AKS) or "gcp" (GKE): OMIT the entire annotations block —
  # a type:LoadBalancer Service already gets a public IP, and the AWS annotation is silently
  # ignored on AKS/GKE. Do NOT copy it onto Azure/GCP.
  annotations:                                                            # ← AWS ONLY — delete for azure/gcp
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing  # ← AWS ONLY — delete for azure/gcp
spec:
  selector:
    app: grafana
  ports:
  - port: 3000
    targetPort: 3000
  type: LoadBalancer
```

For **Azure (AKS)** and **GCP (GKE)** the Service is identical but carries **NO `annotations:` block** at all:
```yaml
metadata:
  name: grafana
  namespace: monitoring
  labels:
    project_id: <project_id>
    component: grafana
  # no annotations — type: LoadBalancer gets a public IP by default on AKS/GKE
spec:
  type: LoadBalancer
  # ... ports/selector identical ...
```

> **Why (AWS only):** Without `aws-load-balancer-scheme: internet-facing`, the AWS Load Balancer Controller defaults to an *internal* load balancer and looks for subnets tagged `kubernetes.io/role/internal-elb`. Default VPC subnets are public and carry `kubernetes.io/role/elb` instead, causing `FailedBuildModel` and a permanently `<pending>` EXTERNAL-IP. The annotation forces an internet-facing NLB. **This rationale is AWS-specific — AKS/GKE provision a public LB without any annotation, so adding it there is wrong.**

---

### 3.3 prometheus_deployment.yaml — 4 OBJECTS: Prometheus Deployment + Service + Pushgateway Deployment + Service
`prometheus_deployment.yaml` MUST contain all four resources in a single file, separated by `---`. Missing the Pushgateway Deployment or Service causes `push_to_gateway()` in the pipeline script to fail with a connection error at runtime.

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

Replace `<k8s_service_account_name>` from `ORCHESTRATION.service_account`, `<iam_role_name>` from `CLOUD_SETUP.iam_role_name`, and `<account_id>` from `CLOUD_SETUP.aws_account_id` — all are static values provided in context. Never derive `<account_id>` from the ECR URL and never write a `<...>` placeholder — the validator will reject it.

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

**Azure (file metastore + ABFS):** authenticate ADLS Gen2 with the **storage account access
key** — NOT an `oauth2` block (a managed identity has no client secret, so the old
`oauth2.client-id=<managed_identity_client_id>` + `credential=<client_secret>` combination is
invalid). The deploy workflow injects the real key in place of the `__ABFS_KEY__` sentinel
(`KEY=$(az storage account keys list ...)` then `sed`), exactly like the ECR-URL sentinel.
```properties
connector.name=hive
hive.metastore=file
hive.metastore.catalog.dir=abfss://<container>@<account>.dfs.core.windows.net/metastore/
hive.azure.abfs-storage-account=<storage_account_name>
hive.azure.abfs-access-key=__ABFS_KEY__
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