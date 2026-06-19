"""Deterministic artifact generation — code-owned artifacts the LLM used to copy.

Per the LLM-vs-deterministic boundary (CLAUDE.md): the LLM owns judgment under
variability (schema → pandas/SQL); deterministic code owns everything mechanically
determined. The artifacts rendered here have ZERO open inputs — every value comes
from the pipeline config — so an LLM step for them was pure cost/latency/variance
(and the source of the repair/validator pressure documented in the standards).

Renders are pure functions (unit-tested against the v1.0.0 golden artifacts in
tests/goldens/). The ensure_* orchestrators write to disk, run the same
validate_generated_code safety net the LLM path used, and report what they wrote.

The knowledge_base standards for these artifacts remain the SPEC (and the Medic's
diagnostic reference) — this module is their executable form. When a standard for a
code-owned artifact changes, change the render here in the same commit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess

from agents.tools import (
    REPO_ROOT,
    _canonical_lakeview_dashboard,
    validate_generated_code,
)
from utils.cloud_config import cloud_get_infra

logger = logging.getLogger("CODEGEN")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _rfc1123(name: str) -> str:
    """K8s resource names: lowercase + hyphens (RFC 1123)."""
    return (name or "").replace("_", "-").lower()


def _pipeline_slug(pipe_conf: dict) -> str:
    """Human slug for dashboard uid/tags: project_name minus the '-insights' suffix
    (the convention both the hand-written and the NL-generated configs follow),
    falling back to the rfc1123 pipeline_id."""
    base = pipe_conf.get("project_name") or _rfc1123(pipe_conf.get("pipeline_id", "pipeline"))
    return base[: -len("-insights")] if base.endswith("-insights") else base


def _cloud_setup(pipe_conf: dict, cloud: str) -> dict:
    return pipe_conf.get(f"{cloud}_setup", {}) or {}


def _destination_uri(pipe_conf: dict, cloud: str) -> str:
    setup = _cloud_setup(pipe_conf, cloud)
    if cloud == "aws":
        return f"s3://{setup.get('bucket_name', '')}/processed/"
    if cloud == "gcp":
        return f"gs://{setup.get('bucket_name', '')}/processed/"
    if cloud == "azure":
        return (
            f"abfss://{setup.get('container_name', '')}@"
            f"{setup.get('storage_account_name', '')}.dfs.core.windows.net/processed/"
        )
    return ""


def _gcp_project(setup: dict) -> str:
    """The GCP PROJECT ID (cloud project, not the pipeline id) — same resolution
    order infra_node uses for the registry URL."""
    return (
        os.getenv(setup.get("project_id_env", "GCP_PROJECT_ID"))
        or os.getenv("GCP_PROJECT_ID", "")
        or (cloud_get_infra("gcp", "project_id") or "")
    )


def _tf_output(name: str) -> str:
    """Read a pipeline-terraform output (terraform/ has already applied by the time
    the orchestration artifacts are generated). Empty string on any failure."""
    try:
        res = subprocess.run(
            ["terraform", "-chdir=terraform", "output", "-raw", name],
            capture_output=True, text=True, timeout=60,
        )
        return res.stdout.strip() if res.returncode == 0 else ""
    except Exception:
        return ""


def _write_text(relpath: str, content: str) -> str:
    """Write content to a repo-relative path, creating directories. Returns the path."""
    directory = os.path.dirname(relpath)
    if directory:
        os.makedirs(directory, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    with open(relpath, "w", encoding="utf-8") as fh:
        fh.write(content)
    return relpath


# ──────────────────────────────────────────────────────────────────────────────
# Renders — architect-owned artifacts
# ──────────────────────────────────────────────────────────────────────────────

def render_lakeview_dashboard(catalog: str, schema: str, table: str) -> str:
    """Lakeview observability dashboard — canonical structure, audit table from config.
    (Completes the migration: previously the LLM emitted this and the structure was
    rebuilt anyway, keeping only the table name — now there is no LLM call at all.)"""
    return _canonical_lakeview_dashboard(f"{catalog}.{schema}.{table}_audit")


_REQUIREMENTS_SHARED = ["pandas", "sqlalchemy", "pyarrow", "trino", "prometheus-client"]

_REQUIREMENTS_CLOUD = {
    # object-storage SDK · to_parquet() filesystem driver · DB driver
    "aws":   ["boto3", "s3fs"],
    "gcp":   ["google-cloud-storage", "gcsfs"],
    "azure": ["azure-storage-blob", "adlfs"],
}

_DB_DRIVER = {
    "postgres": "psycopg2-binary",
    "postgresql": "psycopg2-binary",
    "mysql": "pymysql",
    "mssql": "pyodbc",
}


def render_requirements(cloud: str, db_type: str) -> str:
    """requirements.txt for the pipeline image: shared block + the active cloud's
    storage/filesystem drivers + the SOURCE engine's DB driver (python_standards.md)."""
    driver = _DB_DRIVER.get((db_type or "postgres").lower(), "psycopg2-binary")
    lines = _REQUIREMENTS_SHARED + _REQUIREMENTS_CLOUD.get(cloud, []) + [driver]
    return "\n".join(lines)


def render_dockerfile(script_path: str) -> str:
    """Pipeline image Dockerfile (dockerfile_standard.md) — the only variable is the
    entry-point script path."""
    return f"""# Dockerfile for the data pipeline
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
COPY utils/ utils/
COPY sql/ sql/
COPY dashboards/ dashboards/

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

CMD ["python", "{script_path}"]"""


def _grafana_panel_expr(metric: str) -> str:
    return (
        f'{metric}{{cloud_provider=~"$cloud_provider", project_id=~"$project_id"}}'
    )


def render_monitoring_specs(pipe_conf: dict, cloud: str) -> str:
    """Grafana dashboard JSON (grafana_standards.md): 5 fixed panels, stable uid from
    the pipeline slug, $cloud_provider/$project_id template variables. No 'alerting'
    object — Grafana 9+ ignores it (alert rules are not dashboard JSON)."""
    slug = _pipeline_slug(pipe_conf)
    title = " ".join(w.upper() if w in ("eu", "us", "uk") else w.capitalize()
                     for w in slug.split("-"))
    rate_expr = (
        "pipeline_rows_rejected_total{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}"
        " / clamp_min(pipeline_rows_processed_total{cloud_provider=~\"$cloud_provider\","
        " project_id=~\"$project_id\"} + pipeline_rows_rejected_total{cloud_provider=~\"$cloud_provider\","
        " project_id=~\"$project_id\"}, 1) * 100"
    )
    legend = "{{cloud_provider}} / {{project_id}}"
    # Grafana caps the dashboard uid at 40 chars and silently REFUSES to provision a longer
    # one ("uid too long, max 40 characters" → empty Dashboards). Keep the readable
    # "<slug>-data-observability" form when it fits; for a long pipeline name, fall back to a
    # stable, collision-safe id (slug prefix + short deterministic hash) that stays ≤40 and is
    # identical across runs of the same pipeline.
    _uid = f"{slug}-data-observability"
    if len(_uid) > 40:
        _uid = f"{slug[:31]}-{hashlib.sha1(slug.encode()).hexdigest()[:8]}"
    dashboard = {
        "uid": _uid,
        "title": f"{title} Data Observability",
        "schemaVersion": 37,
        "version": 1,
        "refresh": "5m",
        "time": {"from": "now-6h", "to": "now"},
        "tags": ["data-pipeline", slug, cloud],
        "templating": {"list": [
            {
                "name": "cloud_provider", "type": "query", "datasource": "Prometheus",
                "query": "label_values(pipeline_rows_processed_total, cloud_provider)",
                "refresh": 2, "includeAll": True, "multi": True, "label": "Cloud",
            },
            {
                "name": "project_id", "type": "query", "datasource": "Prometheus",
                "query": 'label_values(pipeline_rows_processed_total{cloud_provider=~"$cloud_provider"}, project_id)',
                "refresh": 2, "includeAll": True, "label": "Pipeline Run",
            },
        ]},
        "panels": [
            {
                "id": 1, "title": "Record Count", "type": "stat", "datasource": "Prometheus",
                "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8},
                "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "green"}, "graphMode": "area"}},
                "targets": [{"expr": _grafana_panel_expr("pipeline_rows_processed_total"), "legendFormat": legend}],
            },
            {
                "id": 2, "title": "Last Success", "type": "stat", "datasource": "Prometheus",
                "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8},
                "fieldConfig": {"defaults": {"unit": "time:YYYY-MM-DD HH:mm:ss",
                                             "color": {"mode": "fixed", "fixedColor": "blue"}}},
                "targets": [{"expr": _grafana_panel_expr("pipeline_last_success_timestamp") + " * 1000",
                             "legendFormat": legend}],
            },
            {
                "id": 3, "title": "Rejection Rate", "type": "stat", "datasource": "Prometheus",
                "gridPos": {"x": 0, "y": 8, "w": 12, "h": 8},
                "options": {"colorMode": "value", "graphMode": "none", "textMode": "value"},
                "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100,
                                             "thresholds": {"mode": "absolute", "steps": [
                                                 {"color": "green", "value": None},
                                                 {"color": "yellow", "value": 20},
                                                 {"color": "red", "value": 50}]}}},
                "targets": [{"expr": rate_expr, "legendFormat": legend}],
            },
            {
                "id": 4, "title": "Run Duration", "type": "gauge", "datasource": "Prometheus",
                "gridPos": {"x": 12, "y": 8, "w": 12, "h": 8},
                "fieldConfig": {"defaults": {"unit": "s",
                                             "thresholds": {"mode": "absolute", "steps": [
                                                 {"color": "green", "value": None},
                                                 {"color": "yellow", "value": 60},
                                                 {"color": "red", "value": 120}]}}},
                "targets": [{"expr": _grafana_panel_expr("pipeline_duration_seconds"), "legendFormat": legend}],
            },
            {
                "id": 5, "title": "Rejections by Reason", "type": "piechart", "datasource": "Prometheus",
                "gridPos": {"x": 0, "y": 16, "w": 24, "h": 8},
                "options": {"pieType": "pie",
                            "legend": {"displayMode": "table", "placement": "right",
                                       "values": ["value", "percent"]},
                            "reduceOptions": {"values": False, "calcs": ["lastNotNull"]}},
                "targets": [{"expr": _grafana_panel_expr("pipeline_rows_rejected_by_reason"),
                             "legendFormat": "{{reason}}"}],
            },
        ],
    }
    return json.dumps(dashboard, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# Renders — K8s manifests (k8s_deployment_rules.md)
# ──────────────────────────────────────────────────────────────────────────────

def _sa_block(pipe_conf: dict, cloud: str) -> str:
    """Cloud-specific ServiceAccount (k8s standard §8.1–8.3)."""
    setup = _cloud_setup(pipe_conf, cloud)
    sa_name = setup.get("k8s_service_account_name", "pipeline-sa")
    if cloud == "aws":
        account_id = setup.get("aws_account_id", "")
        role = setup.get("iam_role_name", "")
        return f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {sa_name}
  namespace: analytics
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::{account_id}:role/{role}"""
    if cloud == "azure":
        # Primary: the pipeline terraform's output (it has just applied). Fallbacks try
        # BOTH key spellings: the pipeline output name and the bootstrap output name
        # (crm_managed_identity_client_id in .bootstrap_outputs.json for local dev).
        client_id = (
            _tf_output("managed_identity_client_id")
            or (cloud_get_infra("azure", "managed_identity_client_id") or "")
            or (cloud_get_infra("azure", "crm_managed_identity_client_id") or "")
        )
        return f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {sa_name}
  namespace: analytics
  annotations:
    azure.workload.identity/client-id: "{client_id}"
  labels:
    azure.workload.identity/use: "true\""""
    # gcp
    email = f"{setup.get('service_account_id', sa_name)}@{_gcp_project(setup)}.iam.gserviceaccount.com"
    return f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {sa_name}
  namespace: analytics
  annotations:
    iam.gke.io/gcp-service-account: "{email}\""""


def render_namespaces(pipe_conf: dict, cloud: str) -> str:
    return f"""apiVersion: v1
kind: Namespace
metadata:
  name: analytics
---
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
{_sa_block(pipe_conf, cloud)}"""


def render_trino_deployment(pipe_conf: dict, cloud: str) -> str:
    setup = _cloud_setup(pipe_conf, cloud)
    sa_name = setup.get("k8s_service_account_name", "pipeline-sa")
    shared_label = pipe_conf.get("project_folder_name", "multi-cloud-self-healing-agent")
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: trino
  namespace: analytics
  labels:
    project_id: {shared_label}
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
        project_id: {shared_label}
        component: trino
    spec:
      serviceAccountName: {sa_name}
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
    project_id: {shared_label}
    component: trino
spec:
  selector:
    app: trino
  ports:
  - port: 8080
    targetPort: 8080
  type: ClusterIP"""


def render_grafana_deployment(pipe_conf: dict, cloud: str) -> str:
    shared_label = pipe_conf.get("project_folder_name", "multi-cloud-self-healing-agent")
    # AWS-only: force an internet-facing NLB (default VPC subnets lack the internal-elb
    # tag → internal LB would stay <pending>). AKS/GKE get a public IP with no annotation.
    annotations = (
        "\n  annotations:\n"
        "    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing"
        if cloud == "aws" else ""
    )
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana
  namespace: monitoring
  labels:
    project_id: {shared_label}
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
        project_id: {shared_label}
        component: grafana
    spec:
      containers:
      - name: grafana
        image: grafana/grafana:10.4.2
        ports:
        - containerPort: 3000
        env:
        - name: GF_USERS_DEFAULT_LANGUAGE
          value: "en-US"
        - name: GF_SECURITY_ADMIN_PASSWORD
          valueFrom:
            secretKeyRef:
              name: grafana-admin
              key: admin-password
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
    project_id: {shared_label}
    component: grafana{annotations}
spec:
  selector:
    app: grafana
  ports:
  - port: 3000
    targetPort: 3000
  type: LoadBalancer"""


def render_prometheus_deployment(pipe_conf: dict, cloud: str) -> str:
    shared_label = pipe_conf.get("project_folder_name", "multi-cloud-self-healing-agent")
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
  namespace: monitoring
  labels:
    project_id: {shared_label}
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
        project_id: {shared_label}
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
    project_id: {shared_label}
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
    project_id: {shared_label}
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
        project_id: {shared_label}
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
    project_id: {shared_label}
    component: pushgateway
spec:
  selector:
    app: pushgateway
  ports:
  - port: 9091
    targetPort: 9091
  type: ClusterIP"""


def _hive_properties(pipe_conf: dict, cloud: str) -> str:
    """hive.properties content per cloud (k8s standard §8.4)."""
    setup = _cloud_setup(pipe_conf, cloud)
    if cloud == "aws":
        region = setup.get("region", "")
        return (
            "connector.name=hive\n"
            "hive.metastore=glue\n"
            f"hive.metastore.glue.region={region}\n"
            f"hive.s3.region={region}\n"
            "hive.s3.path-style-access=true\n"
            "hive.allow-drop-table=true\n"
            "hive.allow-rename-table=true"
        )
    if cloud == "azure":
        account = setup.get("storage_account_name", "")
        container = setup.get("container_name", "")
        return (
            "connector.name=hive\n"
            "hive.metastore=file\n"
            f"hive.metastore.catalog.dir=abfss://{container}@{account}.dfs.core.windows.net/metastore/\n"
            f"hive.azure.abfs-storage-account={account}\n"
            "hive.azure.abfs-access-key=__ABFS_KEY__\n"
            "hive.allow-drop-table=true\n"
            "hive.allow-rename-table=true"
        )
    # gcp
    bucket = setup.get("bucket_name", "")
    return (
        "connector.name=hive\n"
        "hive.metastore=file\n"
        f"hive.metastore.catalog.dir=gs://{bucket}/metastore/\n"
        "hive.gcs.use-access-token=false\n"
        "hive.allow-drop-table=true\n"
        "hive.allow-rename-table=true"
    )


def _indent_block(text: str, pad: str) -> str:
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def render_configmaps(pipe_conf: dict, cloud: str) -> str:
    """All 5 ConfigMaps. The two large artifacts (setup_trino.sql,
    monitoring_specs.json) are embedded VERBATIM from the architect's files on disk —
    same single-source-of-truth guarantee the old __EMBED_*__ token path provided."""
    shared_label = pipe_conf.get("project_folder_name", "multi-cloud-self-healing-agent")
    sql_content = ""
    if os.path.exists("sql/setup_trino.sql"):
        with open("sql/setup_trino.sql", encoding="utf-8") as fh:
            sql_content = fh.read().rstrip("\n")
    specs_content = ""
    if os.path.exists("dashboards/monitoring_specs.json"):
        with open("dashboards/monitoring_specs.json", encoding="utf-8") as fh:
            specs_content = fh.read().rstrip("\n")

    return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: trino-sql-config
  namespace: analytics
  labels:
    project_id: {shared_label}
data:
  setup_trino.sql: |
{_indent_block(sql_content, '    ')}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: hive-catalog-config
  namespace: analytics
  labels:
    project_id: {shared_label}
data:
  hive.properties: |
{_indent_block(_hive_properties(pipe_conf, cloud), '    ')}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dash-config
  namespace: monitoring
  labels:
    project_id: {shared_label}
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
{_indent_block(specs_content, '    ')}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasource-config
  namespace: monitoring
  labels:
    project_id: {shared_label}
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
    project_id: {shared_label}
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
    scrape_configs:
      - job_name: pushgateway
        static_configs:
          - targets: ['pushgateway.monitoring.svc.cluster.local:9091']
        honor_labels: true"""


def render_job(pipe_conf: dict, cloud: str, registry_url: str) -> str:
    setup = _cloud_setup(pipe_conf, cloud)
    pipeline_id = pipe_conf.get("pipeline_id", "pipeline")
    name = _rfc1123(pipeline_id)
    sa_name = setup.get("k8s_service_account_name", "pipeline-sa")
    # GCP pulls :latest directly (the build pushes it; a tag-rewrite sed is fragile when
    # the image name varies). AWS/Azure CI anchors a sed on the URL and rewrites the tag
    # to ${{ github.sha }} — any initial tag works; :latest keeps the three clouds uniform.
    image = f"{registry_url}:latest" if registry_url else "RESOLVE_FROM_EXECUTE_TERRAFORM_OUTPUT"
    azure_wi_label = (
        '\n        azure.workload.identity/use: "true"' if cloud == "azure" else ""
    )
    return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: {name}-job
  namespace: analytics
  labels:
    project_id: {pipeline_id}
    component: pipeline-job
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        project_id: {pipeline_id}
        component: pipeline-job{azure_wi_label}
    spec:
      serviceAccountName: {sa_name}
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
        image: {image}
        env:
        - name: PROJECT_ID
          value: "{pipeline_id}"
        - name: CLOUD_PROVIDER
          value: "{cloud}"
        - name: DESTINATION_URI
          value: "{_destination_uri(pipe_conf, cloud)}"
        - name: TRINO_HOST
          value: "trino.analytics.svc.cluster.local"
        - name: PUSHGATEWAY_URL
          value: "http://pushgateway.monitoring.svc.cluster.local:9091"
        envFrom:
        - secretRef:
            name: {name}-db-credentials
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
          name: trino-sql-config"""


_K8S_RENDERS = {
    "k8s/00_namespaces.yaml": render_namespaces,
    "k8s/trino_deployment.yaml": render_trino_deployment,
    "k8s/grafana_deployment.yaml": render_grafana_deployment,
    "k8s/prometheus_deployment.yaml": render_prometheus_deployment,
    "k8s/configmaps.yaml": render_configmaps,
}


# ──────────────────────────────────────────────────────────────────────────────
# Renders — GitHub Actions deploy workflow (cicd_standards.md)
# ──────────────────────────────────────────────────────────────────────────────

_WF_HEADER = """name: Deploy Pipeline

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

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
"""

_WF_GRAFANA_SECRET = """
      - name: Create Grafana Admin Secret
        env:
          GRAFANA_ADMIN_PASSWORD: ${{ secrets.GRAFANA_ADMIN_PASSWORD }}
        run: |
          kubectl apply -f k8s/00_namespaces.yaml
          kubectl get secret grafana-admin -n monitoring >/dev/null 2>&1 || \\
            kubectl create secret generic grafana-admin -n monitoring \\
              --from-literal=admin-password="${GRAFANA_ADMIN_PASSWORD:-$(openssl rand -base64 24)}"
"""

_WF_DEPLOY_SHARED = """
      - name: Deploy Shared Services to Kubernetes
        run: |
          kubectl apply -f k8s/00_namespaces.yaml
          kubectl apply -f k8s/configmaps.yaml
          kubectl apply -f k8s/prometheus_deployment.yaml
          kubectl apply -f k8s/trino_deployment.yaml
          kubectl apply -f k8s/grafana_deployment.yaml
          kubectl rollout restart deployment/trino -n analytics
          kubectl rollout restart deployment/grafana -n monitoring
          kubectl rollout status deployment/trino -n analytics --timeout=120s
          kubectl rollout status deployment/grafana -n monitoring --timeout=120s
"""

_WF_DEPLOY_JOB_AND_STATUS = """
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
              echo "Job completed successfully"; break
            fi
            if [ "${FAILED:-0}" -gt 0 ]; then
              echo "Job failed. Fetching logs..."
              echo "=== init-trino logs ==="
              kubectl logs -l component=pipeline-job -n analytics -c init-trino --tail=50 2>/dev/null || echo "(init-trino logs unavailable)"
              echo "=== pipeline logs ==="
              kubectl logs -l component=pipeline-job -n analytics -c pipeline --tail=100 2>/dev/null || echo "(pipeline logs unavailable)"
              exit 1
            fi
            if [ "$i" = "60" ]; then
              echo "Timeout waiting for job"
              echo "=== init-trino logs ==="
              kubectl logs -l component=pipeline-job -n analytics -c init-trino --tail=50 2>/dev/null || echo "(init-trino logs unavailable)"
              echo "=== pipeline logs ==="
              kubectl logs -l component=pipeline-job -n analytics -c pipeline --tail=100 2>/dev/null || echo "(pipeline logs unavailable)"
              exit 1
            fi
            echo "Waiting for job... ($i/60)"; sleep 10
          done

      - run: echo "Deployment Complete"
"""


def _render_workflow_aws(pipe_conf: dict, registry_url: str) -> str:
    setup = _cloud_setup(pipe_conf, "aws")
    cluster = setup.get("eks_cluster_name", "")
    url = registry_url
    return (_WF_HEADER + f"""
      - name: Configure AWS Credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{{{ secrets.AWS_ACCESS_KEY_ID }}}}
          aws-secret-access-key: ${{{{ secrets.AWS_SECRET_ACCESS_KEY }}}}
          aws-region: ${{{{ vars.AWS_DEFAULT_REGION }}}}

      - name: Login to Amazon ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build & Push Image
        run: |
          docker build -t {url}:${{{{ github.sha }}}} -f Dockerfile .
          docker push {url}:${{{{ github.sha }}}}
          docker tag {url}:${{{{ github.sha }}}} {url}:latest
          docker push {url}:latest

      - name: Update Kubeconfig
        run: aws eks update-kubeconfig --region ${{{{ vars.AWS_DEFAULT_REGION }}}} --name {cluster}

      - name: Set Image Tag in Job Manifest
        run: |
          sed -i 's|image: {url}.*|image: {url}:${{{{ github.sha }}}}|' k8s/job.yaml
""" + _WF_GRAFANA_SECRET + _WF_DEPLOY_SHARED + f"""
      - name: Create DB Credentials Secret
        run: |
          kubectl create secret generic {_rfc1123(pipe_conf.get("pipeline_id", ""))}-db-credentials -n analytics \\
            --dry-run=client -o yaml | kubectl apply -f -
""" + _WF_DEPLOY_JOB_AND_STATUS)


def _render_workflow_azure(pipe_conf: dict, registry_url: str) -> str:
    setup = _cloud_setup(pipe_conf, "azure")
    rg = setup.get("resource_group_name", "")
    aks = setup.get("aks_cluster_name", "")
    storage = setup.get("storage_account_name", "")
    # registry_url arrives as the FULL image reference (host/image — infra_node appends
    # the segment for azure, same as gcp). Derive the bare ACR host only for `az acr login`.
    image = registry_url or (
        f"{setup.get('acr_login_server', '')}/{_rfc1123(pipe_conf.get('pipeline_id', ''))}"
    )
    acr_host = image.split("/")[0]
    secret_name = f"{_rfc1123(pipe_conf.get('pipeline_id', ''))}-db-credentials"
    return (_WF_HEADER + f"""
      - name: Azure Login
        uses: azure/login@v2
        with:
          creds: ${{{{ secrets.AZURE_CREDENTIALS }}}}

      - name: ACR Login
        run: |
          REG="$(echo '{acr_host}' | cut -d'.' -f1)"
          for i in 1 2 3; do
            az acr login --name "$REG" && break || {{ echo "ACR login attempt $i failed (transient), retrying in 10s..."; sleep 10; }}
          done

      - name: Build Azure Storage Connection String + inject Trino ABFS key
        run: |
          KEY=$(az storage account keys list -g {rg} -n {storage} --query '[0].value' -o tsv)
          echo "AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName={storage};AccountKey=$KEY;EndpointSuffix=core.windows.net" >> "$GITHUB_ENV"
          sed -i "s|__ABFS_KEY__|$KEY|g" k8s/configmaps.yaml

      - name: Build and Push Docker Image
        run: |
          docker build -t {image}:${{{{ github.sha }}}} -f Dockerfile .
          docker push {image}:${{{{ github.sha }}}}
          docker tag {image}:${{{{ github.sha }}}} {image}:latest
          docker push {image}:latest

      - name: Update Kubeconfig
        run: az aks get-credentials --resource-group {rg} --name {aks}

      - name: Set Image Tag in Job Manifest
        run: |
          sed -i 's|image: {image}:.*|image: {image}:${{{{ github.sha }}}}|' k8s/job.yaml
""" + _WF_GRAFANA_SECRET + _WF_DEPLOY_SHARED + f"""
      - name: Create DB Credentials Secret
        run: |
          kubectl create secret generic {secret_name} -n analytics \\
            --from-literal=CRM_DB_HOST=${{{{ vars.CRM_DB_HOST }}}} \\
            --from-literal=CRM_DB_PORT=${{{{ vars.CRM_DB_PORT }}}} \\
            --from-literal=CRM_DB_USER=${{{{ vars.CRM_DB_USER }}}} \\
            --from-literal=CRM_DB_NAME=${{{{ vars.CRM_DB_NAME }}}} \\
            --from-literal=CRM_DB_PASSWORD=${{{{ secrets.AZURE_DB_PASSWORD }}}} \\
            --from-literal=AZURE_STORAGE_CONNECTION_STRING="$AZURE_STORAGE_CONNECTION_STRING" \\
            --dry-run=client -o yaml | kubectl apply -f -
""" + _WF_DEPLOY_JOB_AND_STATUS)


def _render_workflow_gcp(pipe_conf: dict, registry_url: str) -> str:
    setup = _cloud_setup(pipe_conf, "gcp")
    cluster = setup.get("gke_cluster_name", "")
    region = setup.get("region", "")
    ar_region = setup.get("artifact_registry_region", region)
    url = registry_url
    secret_name = f"{_rfc1123(pipe_conf.get('pipeline_id', ''))}-db-credentials"
    return (_WF_HEADER + f"""
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{{{ secrets.GCP_SA_KEY_JSON }}}}

      - name: Set up gcloud + GKE auth plugin
        uses: google-github-actions/setup-gcloud@v2
        with:
          install_components: 'gke-gcloud-auth-plugin'

      - name: Configure Docker for Artifact Registry
        run: gcloud auth configure-docker {ar_region}-docker.pkg.dev --quiet

      - name: Build & Push Image
        run: |
          docker build -t {url}:${{{{ github.sha }}}} -f Dockerfile .
          docker push {url}:${{{{ github.sha }}}}
          docker tag {url}:${{{{ github.sha }}}} {url}:latest
          docker push {url}:latest

      - name: Update Kubeconfig
        run: gcloud container clusters get-credentials {cluster} --region {region} --project ${{{{ vars.GCP_PROJECT_ID }}}}
""" + _WF_GRAFANA_SECRET + _WF_DEPLOY_SHARED + f"""
      - name: Create DB Credentials Secret
        run: |
          kubectl create secret generic {secret_name} -n analytics \\
            --from-literal=MYSQL_DB_HOST=${{{{ vars.MYSQL_DB_HOST }}}} \\
            --from-literal=MYSQL_DB_PORT=${{{{ vars.MYSQL_DB_PORT }}}} \\
            --from-literal=MYSQL_DB_USER=${{{{ vars.MYSQL_DB_USER }}}} \\
            --from-literal=MYSQL_DB_NAME=${{{{ vars.MYSQL_DB_NAME }}}} \\
            --from-literal=MYSQL_DB_PASSWORD=${{{{ secrets.MYSQL_DB_PASSWORD }}}} \\
            --dry-run=client -o yaml | kubectl apply -f -
""" + _WF_DEPLOY_JOB_AND_STATUS)


def _render_workflow_databricks(pipe_conf: dict) -> str:
    pipeline_id = pipe_conf.get("pipeline_id", "pipeline")
    return f"""name: Deploy Pipeline
on:
  push:
    paths: ['scripts/pipe_*.py', 'sql/**', 'terraform/**']

permissions:
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    env:
      DATABRICKS_HOST:          ${{{{ secrets.DATABRICKS_HOST }}}}
      DATABRICKS_CLIENT_ID:     ${{{{ secrets.DATABRICKS_CLIENT_ID }}}}
      DATABRICKS_CLIENT_SECRET: ${{{{ secrets.DATABRICKS_CLIENT_SECRET }}}}
      AWS_ACCESS_KEY_ID:     ${{{{ secrets.AWS_ACCESS_KEY_ID }}}}
      AWS_SECRET_ACCESS_KEY: ${{{{ secrets.AWS_SECRET_ACCESS_KEY }}}}
      AWS_DEFAULT_REGION:    ${{{{ vars.AWS_DEFAULT_REGION }}}}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_wrapper: false
      - uses: databricks/setup-cli@v1.2.1

      - name: Upload Spark script to DBFS
        run: |
          databricks fs mkdirs "dbfs:/pipelines/{pipeline_id}" || true
          databricks fs cp scripts/{pipeline_id}.py "dbfs:/pipelines/{pipeline_id}/{pipeline_id}.py" --overwrite

      - name: Trigger job run and wait
        working-directory: terraform
        run: |
          terraform init -input=false
          JOB_ID=$(terraform output -raw job_id)
          # --no-wait: return the run_id IMMEDIATELY so the poll loop can track it. Without it
          # the CLI blocks until the run is terminal and, on failure, exits non-zero with an
          # EMPTY stdout → RUN_ID="" → the loop dies with "invalid RUN_ID" and the real error
          # never surfaces.
          RUN_ID=$(databricks jobs run-now "$JOB_ID" --no-wait -o json | jq -r '.run_id')
          echo "Triggered run $RUN_ID for job $JOB_ID"
          for i in $(seq 1 80); do
            RUN=$(databricks jobs get-run "$RUN_ID" -o json)
            STATE=$(echo "$RUN" | jq -r '.state.life_cycle_state')
            RESULT=$(echo "$RUN" | jq -r '.state.result_state // empty')
            echo "run $RUN_ID: $STATE $RESULT ($i/80)"
            if [ "$STATE" = "TERMINATED" ] || [ "$STATE" = "INTERNAL_ERROR" ]; then
              [ "$RESULT" = "SUCCESS" ] && {{ echo "Job succeeded"; exit 0; }}
              # The job-level state is only the generic "Workload failed, see run output for
              # details" — the ROOT CAUSE (the Spark task's exception + traceback) lives in the
              # TASK run output. Print it so the Medic's CI-log fetch sees the real error and can
              # diagnose/route it (e.g. "Secret does not exist …" → infra) instead of guessing.
              echo "Job failed: $STATE $RESULT"
              TASK_RUN_ID=$(echo "$RUN" | jq -r '.tasks[0].run_id // empty')
              if [ -n "$TASK_RUN_ID" ]; then
                databricks jobs get-run-output "$TASK_RUN_ID" -o json | jq -r '.error // empty, .error_trace // empty'
              fi
              exit 1
            fi
            sleep 15
          done
          echo "Timeout waiting for run"; exit 1

      - run: echo "Deployment Complete\""""


def render_workflow(pipe_conf: dict, cloud: str, registry_url: str, is_databricks: bool) -> str:
    if is_databricks:
        return _render_workflow_databricks(pipe_conf)
    if cloud == "azure":
        return _render_workflow_azure(pipe_conf, registry_url)
    if cloud == "gcp":
        return _render_workflow_gcp(pipe_conf, registry_url)
    return _render_workflow_aws(pipe_conf, registry_url)


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrators — write + validate + track (same safety net as the LLM path)
# ──────────────────────────────────────────────────────────────────────────────

def _write_and_validate(relpath: str, content: str,
                        written: list[str], errors: list[str]) -> None:
    try:
        _write_text(relpath, content)
    except Exception as exc:  # disk errors are real failures, surface them
        errors.append(f"{relpath}: write failed: {exc}")
        return
    verdict = str(validate_generated_code.invoke({"filename": relpath}))
    if "VALIDATION FAILED" in verdict:
        # Generator bug — fail loudly; the medic fix is a code change, not a patch.
        errors.append(f"{relpath}: {verdict}")
        logger.error(f"❌ CODEGEN VALIDATION FAILED: {relpath}")
    else:
        written.append(relpath)
        logger.info(f"✅ CODEGEN: {relpath} written + validated")


def ensure_architect_artifacts(pipe_conf: dict, db_conf: dict, infra_conf: dict,
                               written_files: list[str]) -> tuple[list[str], list[str]]:
    """Write the architect-phase deterministic artifacts that are still missing.
    Returns (newly_written, errors). LLM-owned artifacts (.py, setup_*.sql) are
    untouched — they remain in the architect's implementation phase."""
    new_files: list[str] = []
    errors: list[str] = []
    existing = {f.lower() for f in written_files}
    is_databricks = (infra_conf.get("provider", "") or "").lower() == "databricks"
    cloud = (pipe_conf.get("cloud_provider", "aws") or "aws").lower()

    if is_databricks:
        pipeline_id = pipe_conf.get("pipeline_id", "pipeline").lower()
        lakeview_path = f"dashboards/{pipeline_id}_lakeview.json"
        if lakeview_path.lower() not in existing:
            uc = infra_conf.get("unity_catalog", {}) or {}
            target = pipe_conf.get("databricks_target", {}) or {}
            catalog = target.get("catalog") or uc.get("catalog", "")
            schema = target.get("schema") or uc.get("schema", "raw")
            table = target.get("table_name") or pipeline_id
            _write_and_validate(
                lakeview_path,
                render_lakeview_dashboard(catalog, schema, table),
                new_files, errors,
            )
        return new_files, errors

    if "requirements.txt" not in existing:
        db_type = (db_conf.get("db_type", "postgres") or "postgres").lower()
        _write_and_validate(
            "requirements.txt", render_requirements(cloud, db_type), new_files, errors
        )
    if "dashboards/monitoring_specs.json" not in existing:
        _write_and_validate(
            "dashboards/monitoring_specs.json",
            render_monitoring_specs(pipe_conf, cloud),
            new_files, errors,
        )
    return new_files, errors


def ensure_infra_artifacts(pipe_conf: dict, infra_conf: dict, registry_url: str,
                           written_files: list[str]) -> tuple[list[str], list[str]]:
    """Write the infra-phase deterministic artifacts (Dockerfile, the K8s manifests,
    the deploy workflow) that are still missing. Returns (newly_written, errors).
    Terraform remains LLM-owned (per-cloud resource judgment) — untouched here."""
    new_files: list[str] = []
    errors: list[str] = []
    existing = {f.lower() for f in written_files}
    is_databricks = (infra_conf.get("provider", "") or "").lower() == "databricks"
    cloud = (pipe_conf.get("cloud_provider", "aws") or "aws").lower()
    pipeline_id = pipe_conf.get("pipeline_id", "pipeline").lower()

    if not is_databricks:
        if "dockerfile" not in existing:
            script_path = (pipe_conf.get("project_structure", {}) or {}).get(
                "python_script_path", f"scripts/{pipeline_id}.py"
            )
            _write_and_validate("Dockerfile", render_dockerfile(script_path),
                                new_files, errors)
        for relpath, render in _K8S_RENDERS.items():
            if relpath.lower() not in existing:
                _write_and_validate(relpath, render(pipe_conf, cloud), new_files, errors)
        if "k8s/job.yaml" not in existing:
            _write_and_validate("k8s/job.yaml", render_job(pipe_conf, cloud, registry_url),
                                new_files, errors)

    workflow_rel = f".github/workflows/{pipeline_id}_pipeline.yml"
    if not any(".github/workflows" in f.lower() for f in written_files):
        workflow_abs = os.path.join(str(REPO_ROOT), ".github", "workflows",
                                    f"{pipeline_id}_pipeline.yml")
        # CROSS-TRIGGER GUARD: the repo holds ONE pipeline's artifact set at a time
        # (k8s/, Dockerfile, requirements.txt are shared paths), so a deploy workflow
        # left behind by a PREVIOUS pipeline would fire on THIS pipeline's artifact
        # push and deploy the wrong cloud's manifests to its cluster. Remove every
        # other generated pipe_*_pipeline.yml; push_to_github stages the deletions
        # (git add on the workflows dir). Repo-infrastructure workflows
        # (run_agent/tests/security/...) never match the pipe_* pattern.
        import glob as _glob
        for _stale in _glob.glob(os.path.join(str(REPO_ROOT), ".github", "workflows",
                                              "pipe_*_pipeline.yml")):
            if os.path.basename(_stale) != f"{pipeline_id}_pipeline.yml":
                try:
                    os.remove(_stale)
                    logger.info(f"🧹 CODEGEN: removed stale deploy workflow "
                                f"{os.path.basename(_stale)} (cross-trigger guard)")
                except OSError as _exc:
                    errors.append(f"{_stale}: stale-workflow removal failed: {_exc}")
        content = render_workflow(pipe_conf, cloud, registry_url, is_databricks)
        try:
            os.makedirs(os.path.dirname(workflow_abs), exist_ok=True)
            with open(workflow_abs, "w", encoding="utf-8") as fh:
                fh.write(content if content.endswith("\n") else content + "\n")
            verdict = str(validate_generated_code.invoke({"filename": workflow_abs}))
            if "VALIDATION FAILED" in verdict:
                errors.append(f"{workflow_rel}: {verdict}")
                logger.error(f"❌ CODEGEN VALIDATION FAILED: {workflow_rel}")
            else:
                new_files.append(workflow_rel)
                logger.info(f"✅ CODEGEN: {workflow_rel} written + validated")
        except Exception as exc:
            errors.append(f"{workflow_rel}: write failed: {exc}")

    return new_files, errors
