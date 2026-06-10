# Multi-Cloud Data Engineer Agent — Product Vision

> Forward-looking product framing. For what is built and validated today, see [ARCHITECTURE.md](ARCHITECTURE.md) and the e2e matrix in the [README](../README.md).

## The Pitch (one sentence)

> "Upload your dataset, tell us what you want to do with it — we show you 4 plans with costs and charts, recommend the best fit, and if you say yes we build it automatically."

---

## The Problem We Solve

A data engineer today needs 2 weeks to set up a production pipeline from scratch:
- Terraform for EKS/AKS/GKE/Databricks → 2-3 days
- Kubernetes manifests or Databricks jobs → 1-2 days
- CI/CD pipeline → 1 day
- Monitoring, data quality rules → 1-2 days

And if a requirement changes, they start over.

**With this agent: 10 minutes.** And if the requirement changes, you just re-describe it.

---

## Why We're Different From a Generic LLM

| | ChatGPT | This Agent |
|---|---|---|
| Writes code | ✅ | ✅ |
| Runs & deploys code | ❌ | ✅ |
| Reads your actual dataset | ❌ | ✅ |
| Detects PII automatically | ❌ | ✅ |
| Costs all 4 options | ❌ | ✅ |
| Monitors after deploy | ❌ | ✅ |
| Self-heals on failure | ❌ | ✅ |

> **ChatGPT is the consultant who writes the plan. We're the team that builds it.**

---

## The 4 Deployment Options

| Option | Best for | ~Cost/mo |
|---|---|---|
| 🟠 **AWS** EKS + S3 + RDS | Mature teams, S3-native workloads | ~$120 |
| 🔵 **Azure** AKS + ADLS + PostgreSQL | GDPR/EU compliance, lowest K8s cost | ~$68 |
| 🟢 **GCP** GKE + GCS + Cloud SQL | Analytics-heavy, Trino federation | ~$110 |
| ⚡ **Databricks** Delta Lake + Spark + Unity Catalog | Heavy ETL, ML pipelines, Lakehouse | ~$72 |

**Databricks is recommended when:**
- Data volume > 200 GB/day
- Pipeline includes ML model training (Mosaic AI)
- Complex multi-hop transformations (Spark at scale)
- Need Delta Lake (ACID transactions, time travel, schema evolution)
- Need Unity Catalog for cross-cloud data governance

---

## The Full User Flow

### Step 1 — Upload Dataset
- User uploads CSV / Parquet / JSON
- System auto-detects:
  - Schema (column names, data types)
  - Row count and file size → accurate cost estimation
  - PII fields (emails, phones, names) → auto-suggests masking rules
  - Data quality issues (nulls, duplicates, format violations)

### Step 2 — Describe Requirements
- Plain English: *"Daily sync to cloud for EU analytics, GDPR compliant, includes churn prediction model"*
- Agent extracts: source type, destination, frequency, region, compliance, ML needs, transform complexity

### Step 3 — See 4 Plans
- **Bar chart**: AWS vs Azure vs GCP vs Databricks monthly cost
- **Per-option cards**: pros, cons, cost breakdown
- **Smart recommendation**: agent picks the right option based on your actual requirements
  - GDPR + moderate scale → Azure
  - ML or heavy ETL → Databricks
  - Analytics + Trino → GCP
  - Mature ecosystem → AWS
- **Databricks card**: highlighted separately as the "enterprise lakehouse" option

### Step 4 — Review Business Rules
- Three modes:
  - 📋 **Demo rules** — pre-built rules (sales / crm / marketing domains)
  - 📝 **Extract from description** — GPT extracts rules from the user's text automatically
  - 📁 **Upload file** — user uploads YAML/JSON with their own rules
- Full preview before deployment

### Step 5 — Confirm & Deploy
- User selects an option
- Agent deploys automatically:
  - **AWS/Azure/GCP**: Terraform + Kubernetes + GitHub Actions + Prometheus monitoring
  - **Databricks**: Workspace + Jobs cluster + SQL Warehouse + Delta Lake + Unity Catalog + MLflow
- Self-healing: if something breaks, the Medic agent fixes it

### Step 6 — Monitor
- Observability dashboard: records/day, error rate, latency, SLA %
- Per-cloud performance breakdown
- Trino federation: query data across all clouds with a single SQL statement

---

## What We Have Built

### ✅ Complete
- NL description → structured pipeline config (GPT-4o-mini)
- 4-option cost estimator: AWS / Azure / GCP / Databricks (real pricing)
- Architecture advisor with smart recommendation (ML/ETL → Databricks, GDPR → Azure)
- Dataset upload: schema detection, PII detection, quality issues, auto-rules
- Business rules system: demo / NL extract / file upload (YAML/JSON)
- Full multi-agent system: Supervisor → Architect → Infra → Medic
- Self-healing pipeline
- Streamlit UI: dark theme, 5 tabs
- Trino federation tab (cross-cloud SQL demo)
- Observability dashboard (metrics + charts)
- Bootstrap outputs integration (real Terraform values)
- Databricks end-to-end: bootstrap Terraform (workspace, jobs cluster, serverless SQL warehouse, Unity Catalog), agent-generated Spark/Delta pipeline + `databricks_job` Terraform, Lakeview observability dashboard — **validated e2e 2026-06-08**
- All four platforms validated end-to-end on live infrastructure (AWS & Azure 2026-06-04, GCP 2026-06-06, Databricks 2026-06-08)

### ❌ Still To Build
- MLflow experiment tracking integration (Databricks ML workloads)
- NL authoring for Databricks (today its NL surface is the fixed demo + cost comparison; free-text builder covers the 3 object-storage clouds)
- SaaS hardening: auth, multi-tenancy, billing

---

## Monetisation Options

| Model | Timeline |
|---|---|
| **LinkedIn article** → inbound leads | Now |
| **Consulting service** — you run the agent, deliver the pipeline | Now |
| **SaaS** — auth + multi-tenant + billing | 3-4 months |

---

## The 3 Layers of Value

| Layer | What it means |
|---|---|
| **Speed** | Weeks → minutes for full pipeline setup |
| **Intelligence** | Picks the right cloud AND the right platform for your specific workload |
| **Self-healing** | Pipeline breaks at 3am → agent fixes it, nobody wakes up |
