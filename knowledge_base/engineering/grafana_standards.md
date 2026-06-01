# STANDARD: GRAFANA DASHBOARD GENERATION
When generating `dashboards/monitoring_specs.json`, ensure the following:

## Datasource (Auto-provisioned — do NOT configure manually)
The Prometheus datasource is automatically wired into Grafana via the `grafana-datasource-config` ConfigMap mounted at `/etc/grafana/provisioning/datasources`. It points to `http://prometheus.monitoring.svc.cluster.local:9090`. All panel `datasource` fields must reference it by name: `"datasource": "Prometheus"`. No manual datasource setup in the UI is required or expected.

## Stable Identity (Critical)
- **uid**: Must be derived from the pipeline name only — never from `project_id` or any session-specific value. Use a lowercase hyphenated slug, e.g. `eu-sales-data-observability`. A stable UID ensures `kubectl apply` updates the existing dashboard instead of creating a duplicate on every run.
- **title**: Use a human-readable pipeline name, e.g. `EU Sales Data Observability`. Never include `project_id`.
- **tags**: Use static tags only. MUST include three tags: the generic pipeline type, the pipeline name slug, and the cloud provider. e.g. `["data-pipeline", "eu-sales", "aws"]` for AWS, `["data-pipeline", "us-crm", "azure"]` for Azure, `["data-pipeline", "global-marketing", "gcp"]` for GCP. Never include `project_id`.

## Panel Types
The dashboard MUST contain **exactly five panels — one per emitted metric — each a distinct visualization type**. Never render all panels as `timeseries`. The deprecated `"graph"` type is forbidden in Grafana 8+. Every panel must include `id`, `title`, `type`, `datasource`, `gridPos`, and `targets` with at least one `expr`.

| Panel | Metric | `type` | Color / thresholds |
|---|---|---|---|
| Record Count | `pipeline_rows_processed_total` | `stat` (`sparkline` enabled) | Fixed green (`fieldConfig.defaults.color.mode: fixed`, `fixedColor: green`) |
| Last Success | `pipeline_last_success_timestamp` | `stat` | `unit: "time:YYYY-MM-DD HH:mm:ss"` (multiply the expr by `* 1000` — Grafana time units expect ms). Fixed blue color, NO thresholds. **Do NOT use `dateTimeFromNow`** — its "X ago" text is rendered by moment.js from the viewer's browser locale (e.g. Greek "λίγα δευτερόλεπτα πριν") and CANNOT be forced to English by any server setting (`GF_USERS_DEFAULT_LANGUAGE`, user preference — both verified ineffective). An absolute timestamp is locale-independent. Data-silence is handled by the alerting rule, not panel color. |
| Rejection Rate | `rejected / (processed + rejected) * 100` | `bargauge` (`orientation: horizontal`) | `unit: percent`, `min: 0`, `max: 100`, `color.mode: continuous-GrYlRd`. Show a RATE not the absolute count — a bargauge needs a fixed `max` to fill correctly, and percentage gives a natural 0–100 scale (an absolute count has no meaningful max). Use `clamp_min(..., 1)` in the denominator to avoid divide-by-zero on an empty run. |
| Run Duration | `pipeline_duration_seconds` | `gauge` | `unit: s`; thresholds green `<60`, yellow `60`, red `120` |
| Rejections by Reason | `pipeline_rows_rejected_by_reason` | `piechart` (`pieType: pie`) | One slice per business rule. `legendFormat: "{{reason}}"` so each slice is labelled by the `quality_standards` rule name. Legend `displayMode: table`, `placement: right`, `values: ["value", "percent"]`; `reduceOptions.calcs: ["lastNotNull"]`. This breaks the total Rejection Rate down per rule. A pipeline with no row-removing rules emits zero series → panel shows "No data" (expected, not a bug). |

**2×2 + full-width layout (`gridPos`):** Record Count `{x:0,y:0,w:12,h:8}` · Last Success `{x:12,y:0,w:12,h:8}` · Rejected Rows `{x:0,y:8,w:12,h:8}` · Run Duration `{x:12,y:8,w:12,h:8}` · Rejections by Reason `{x:0,y:16,w:24,h:8}` (full-width bottom row — keeps the existing 2×2 untouched).

Thresholds go in `fieldConfig.defaults.thresholds` with `mode: absolute` and `steps` (each `{color, value}`, base step `value: null`). Colors are Grafana named colors (`green`, `yellow`, `red`).

---

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
  "tags": ["data-pipeline", "eu-sales", "aws"],   ← always 3 tags: type, pipeline-slug, cloud
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
      "type": "stat",
      "datasource": "Prometheus",
      "gridPos": { "x": 0, "y": 0, "w": 12, "h": 8 },
      "fieldConfig": { "defaults": { "color": { "mode": "fixed", "fixedColor": "green" }, "graphMode": "area" } },
      "targets": [
        {
          "expr": "pipeline_rows_processed_total{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}",
          "legendFormat": "{{cloud_provider}} / {{project_id}}"
        }
      ]
    },
    {
      "id": 2,
      "title": "Last Success",
      "type": "stat",
      "datasource": "Prometheus",
      "gridPos": { "x": 12, "y": 0, "w": 12, "h": 8 },
      "fieldConfig": {
        "defaults": {
          "unit": "time:YYYY-MM-DD HH:mm:ss",
          "color": { "mode": "fixed", "fixedColor": "blue" }
        }
      },
      "targets": [
        {
          "expr": "pipeline_last_success_timestamp{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"} * 1000",
          "legendFormat": "{{cloud_provider}} / {{project_id}}"
        }
      ]
    },
    {
      "id": 3,
      "title": "Rejection Rate",
      "type": "bargauge",
      "datasource": "Prometheus",
      "gridPos": { "x": 0, "y": 8, "w": 12, "h": 8 },
      "options": { "orientation": "horizontal" },
      "fieldConfig": {
        "defaults": {
          "unit": "percent",
          "min": 0,
          "max": 100,
          "color": { "mode": "continuous-GrYlRd" }
        }
      },
      "targets": [
        {
          "expr": "pipeline_rows_rejected_total{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"} / clamp_min(pipeline_rows_processed_total{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"} + pipeline_rows_rejected_total{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}, 1) * 100",
          "legendFormat": "{{cloud_provider}} / {{project_id}}"
        }
      ]
    },
    {
      "id": 4,
      "title": "Run Duration",
      "type": "gauge",
      "datasource": "Prometheus",
      "gridPos": { "x": 12, "y": 8, "w": 12, "h": 8 },
      "fieldConfig": {
        "defaults": {
          "unit": "s",
          "thresholds": { "mode": "absolute", "steps": [
            { "color": "green", "value": null },
            { "color": "yellow", "value": 60 },
            { "color": "red", "value": 120 }
          ] }
        }
      },
      "targets": [
        {
          "expr": "pipeline_duration_seconds{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}",
          "legendFormat": "{{cloud_provider}} / {{project_id}}"
        }
      ]
    },
    {
      "id": 5,
      "title": "Rejections by Reason",
      "type": "piechart",
      "datasource": "Prometheus",
      "gridPos": { "x": 0, "y": 16, "w": 24, "h": 8 },
      "options": {
        "pieType": "pie",
        "legend": { "displayMode": "table", "placement": "right", "values": ["value", "percent"] },
        "reduceOptions": { "values": false, "calcs": ["lastNotNull"] }
      },
      "targets": [
        {
          "expr": "pipeline_rows_rejected_by_reason{cloud_provider=~\"$cloud_provider\", project_id=~\"$project_id\"}",
          "legendFormat": "{{reason}}"
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
