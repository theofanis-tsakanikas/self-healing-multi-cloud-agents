# STANDARD: GRAFANA DASHBOARD GENERATION
When generating `dashboards/monitoring_specs.json`, ensure the following:

## Datasource (Auto-provisioned — do NOT configure manually)
The Prometheus datasource is automatically wired into Grafana via the `grafana-datasource-config` ConfigMap mounted at `/etc/grafana/provisioning/datasources`. It points to `http://prometheus.monitoring.svc.cluster.local:9090`. All panel `datasource` fields must reference it by name: `"datasource": "Prometheus"`. No manual datasource setup in the UI is required or expected.

## Stable Identity (Critical)
- **uid**: Must be derived from the pipeline name only — never from `project_id` or any session-specific value. Use a lowercase hyphenated slug, e.g. `eu-sales-data-observability`. A stable UID ensures `kubectl apply` updates the existing dashboard instead of creating a duplicate on every run.
- **title**: Use a human-readable pipeline name, e.g. `EU Sales Data Observability`. Never include `project_id`.
- **tags**: Use static tags only, e.g. `["data-pipeline", "eu-sales"]`. Never include `project_id`.

## Prometheus Queries (Panels)
Do NOT hardcode `project_id` as a static label filter in `expr`. A hardcoded session ID makes every panel show only one historical run.

Use two Grafana template variables so the user can filter interactively by cloud and pipeline run. Declare both `$cloud_provider` and `$project_id` in the `templating` block. Reference both in all `expr` fields so the dashboard works both per-cloud and cross-cloud:

```json
"templating": {
  "list": [
    {
      "name": "cloud_provider",
      "type": "query",
      "datasource": "Prometheus",
      "query": "label_values(pipeline_rows_processed_total, cloud_provider)",
      "refresh": 2,
      "includeAll": true,
      "multi": true,
      "label": "Cloud"
    },
    {
      "name": "project_id",
      "type": "query",
      "datasource": "Prometheus",
      "query": "label_values(pipeline_rows_processed_total{cloud_provider=~\"$cloud_provider\"}, project_id)",
      "refresh": 2,
      "includeAll": true,
      "label": "Pipeline Run"
    }
  ]
},
"panels": [
  {
    "targets": [
      {
        "expr": "pipeline_rows_processed_total{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}",
        "legendFormat": "{{cloud_provider}} / {{project_id}}"
      }
    ]
  }
]
```

**Why both labels matter:** `cloud_provider` groups panels per cloud (aws/azure/gcp). `project_id` filters by pipeline run session. Without `cloud_provider`, the cross-cloud dashboard cannot distinguish EU Sales (AWS) from US CRM (Azure) when both are visible.

## Alerting
Use the pipeline name (not `project_id`) in alert names and annotations:
- `"name": "EU Sales — Data Silence Alert"` ✅
- `"name": "EU_SALES-20260505-0443 — Data Silence Alert"` ❌

Alert labels MUST include both `pipeline` and `cloud_provider` static labels so PagerDuty/Slack notifications show which cloud is affected.

## Full Example Structure
```json
{
  "uid": "eu-sales-data-observability",
  "title": "EU Sales Data Observability",
  "schemaVersion": 37,
  "version": 1,
  "refresh": "5m",
  "time": { "from": "now-6h", "to": "now" },
  "tags": ["data-pipeline", "eu-sales", "aws"],
  "templating": {
    "list": [
      {
        "name": "cloud_provider",
        "type": "query",
        "datasource": "Prometheus",
        "query": "label_values(pipeline_rows_processed_total, cloud_provider)",
        "refresh": 2,
        "includeAll": true,
        "multi": true,
        "label": "Cloud"
      },
      {
        "name": "project_id",
        "type": "query",
        "datasource": "Prometheus",
        "query": "label_values(pipeline_rows_processed_total{cloud_provider=~\"$cloud_provider\"}, project_id)",
        "refresh": 2,
        "includeAll": true,
        "label": "Pipeline Run"
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "Record Count",
      "type": "timeseries",
      "datasource": "Prometheus",
      "gridPos": { "x": 0, "y": 0, "w": 12, "h": 8 },
      "targets": [
        {
          "expr": "pipeline_rows_processed_total{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}",
          "legendFormat": "{{cloud_provider}} / {{project_id}}"
        }
      ]
    },
    {
      "id": 2,
      "title": "Freshness",
      "type": "timeseries",
      "datasource": "Prometheus",
      "gridPos": { "x": 12, "y": 0, "w": 12, "h": 8 },
      "targets": [
        {
          "expr": "pipeline_last_success_timestamp{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}",
          "legendFormat": "{{cloud_provider}} / {{project_id}}"
        }
      ]
    }
  ],
  "alerting": {
    "name": "EU Sales — Data Silence Alert",
    "condition": "no data for 60 minutes",
    "severity": "critical",
    "for": "60m",
    "labels": { "severity": "critical", "pipeline": "eu-sales", "cloud_provider": "aws" },
    "annotations": { "summary": "No data received for EU Sales pipeline (AWS) in the last 60 minutes." }
  }
}
```
