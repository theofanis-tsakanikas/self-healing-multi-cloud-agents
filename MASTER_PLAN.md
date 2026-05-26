# Multi-Cloud Self-Healing Data Engineer Agent — Master Plan

## Vision

A single, cloud-agnostic AI orchestration system that autonomously designs, deploys, and heals data pipelines across **AWS**, **Azure**, and **GCP** simultaneously. Each cloud runs its own Kubernetes cluster (EKS / AKS / GKE) and stores clean data in its native object storage (S3 / ADLS Gen2 / GCS). A shared **Trino** layer federates all three catalogs so **Grafana** can serve cross-cloud dashboards from one endpoint.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GitHub Actions (Orchestrator)                     │
│  Inputs: cloud=[aws|azure|gcp|all], pipeline=[eu_sales|us_crm|...] │
└────────────────┬───────────────┬──────────────────┬─────────────────┘
                 │               │                  │
         ┌───────▼──────┐ ┌─────▼──────┐ ┌────────▼────────┐
         │     AWS       │ │   Azure    │ │      GCP        │
         │ EKS + S3      │ │ AKS + ADLS │ │  GKE + GCS      │
         │ eu-central-1  │ │  eastus    │ │ europe-west3    │
         └───────┬───────┘ └─────┬──────┘ └────────┬────────┘
                 │               │                  │
                 └───────────────┼──────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Trino (multi-catalog)   │
                    │  hive + azure + gcp      │
                    │  catalogs federated      │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Grafana + Prometheus    │
                    │  Cross-cloud dashboards  │
                    └─────────────────────────┘
```

### Agent Orchestration (LangGraph)

```
SUPERVISOR
    ├── ARCHITECT  (Python script, SQL DDL, Grafana JSON)
    ├── INFRA      (Terraform, Dockerfile, Kubernetes manifests, GitHub Actions)
    └── MEDIC      (log analysis, fix requests, partition verification)
```

---

## Repository Structure

```
projects/multi-cloud-self-healing-agent/
├── MASTER_PLAN.md               ← this file
├── main.py                      ← LangGraph entry point
├── graph.py                     ← state machine definition
├── pyproject.toml               ← dependencies (all 3 cloud SDKs)
├── Makefile                     ← bootstrap-aws / bootstrap-azure / bootstrap-gcp
│
├── agents/
│   ├── state.py                 ← AgentState TypedDict
│   ├── tools.py                 ← cloud-agnostic tools
│   ├── supervisor.py
│   ├── architect.py
│   ├── infra.py
│   ├── medic.py
│   └── prompts/
│       ├── supervisor.md
│       ├── architect.md         ← cloud-agnostic (uri/auth from context)
│       ├── infra.md             ← cloud-agnostic (queries all 3 IaC standards)
│       └── medic.md
│
├── bootstrap/
│   ├── aws/                     ← EKS + RDS + S3 state + ECR  [DONE]
│   ├── azure/                   ← AKS + Azure SQL + ADLS + ACR [PHASE 2]
│   └── gcp/                     ← GKE + Cloud SQL + GCS + AR   [PHASE 2]
│
├── configs/
│   ├── business_rules/
│   │   ├── sales_logic.yaml     ← monetary integrity, temporal, currency
│   │   ├── crm_logic.yaml       ← PII hashing, uniqueness, format
│   │   └── marketing_logic.yaml ← taxonomy, CTR, incremental window
│   ├── databases/
│   │   ├── postgres_sales.yaml  ← AWS RDS PostgreSQL
│   │   ├── postgres_crm.yaml    ← Azure PostgreSQL Flexible Server
│   │   └── mysql_marketing.yaml ← GCP Cloud SQL MySQL
│   ├── infra/
│   │   ├── aws_s3.yaml          ← S3 standards (KMS, IRSA, Hive partitioning)
│   │   ├── azure_blob.yaml      ← ADLS Gen2 standards (CMK, workload identity)
│   │   └── gcp_bucket.yaml      ← GCS standards (google-managed, workload identity)
│   ├── pipelines/
│   │   ├── eu_sales_pipeline.yaml       ← AWS S3 target
│   │   ├── eu_sales_objective.md
│   │   ├── us_crm_pipeline.yaml         ← Azure ADLS target
│   │   ├── us_crm_objective.md
│   │   ├── global_marketing_pipeline.yaml  ← GCP GCS target
│   │   └── global_marketing_objective.md
│   └── keys/
│       └── .gitignore
│
├── knowledge_base/
│   ├── engineering/
│   │   ├── python_standards.md  ← all 3 cloud SDKs, idempotency, metrics
│   │   ├── sql_standards.md     ← Trino DDL, multi-catalog naming
│   │   ├── cicd_standards.md    ← GitHub Actions, all 3 auth modules
│   │   └── grafana_standards.md ← dashboard JSON, alerts, federation panels
│   └── infrastructure/
│       ├── terraform_aws_s3.md      ← S3 + DynamoDB backend standard
│       ├── terraform_azure_blob.md  ← ADLS Gen2 + Azure RM backend standard [NEW]
│       ├── terraform_gcp_bucket.md  ← GCS + GCS backend standard [NEW]
│       ├── k8s_deployment_rules.md  ← cloud-agnostic: EKS/AKS/GKE service accounts
│       └── dockerfile_standard.md
│
├── scripts/
│   ├── ingest_to_pinecone.py    ← syncs knowledge_base/ → Pinecone
│   └── seed_chaos.py            ← injects dirty data for testing
│
└── utils/
    ├── config_utils.py
    ├── prompt_utils.py
    ├── file_utils.py
    └── message_utils.py
```

**GitHub Actions (monorepo root):**
```
.github/workflows/multi-cloud-pipeline.yml   ← NEW: replaces self-healing-pipeline.yml
```

---

## Implementation Phases

### Phase 0 — Foundation (DONE in self-healing-data-engineer-agent)
- [x] LangGraph agent orchestration (Supervisor / Architect / Infra / Medic)
- [x] AWS bootstrap: EKS Auto Mode, RDS PostgreSQL, S3 state, ECR, IRSA
- [x] EU Sales pipeline: PostgreSQL → S3 → Trino → Grafana
- [x] Pinecone knowledge base with engineering standards
- [x] GitHub Actions workflow for AWS

---

### Phase 1 — Cloud-Agnostic Scaffold (THIS FOLDER — Current)

**Goal:** Create the new project with the complete structure, new knowledge base files for Azure/GCP, and cloud-selection in GitHub Actions.

#### 1.1 Knowledge Base Additions
- [ ] `terraform_azure_blob.md` — Terraform azurerm: storage account, ADLS Gen2, managed identity, AzureRM backend
- [ ] `terraform_gcp_bucket.md` — Terraform google: GCS bucket, service account, workload identity, GCS backend
- [ ] Update `k8s_deployment_rules.md` — add AKS workload identity + GKE workload identity sections alongside IRSA

#### 1.2 Bootstrap: Azure (`bootstrap/azure/`)
- [ ] `providers.tf` — azurerm + kubernetes providers, azurerm backend (storage account + container)
- [ ] `variables.tf` — resource_group, location, aks_cluster_name, acr_name, db vars
- [ ] `aks.tf` — AKS cluster (Standard tier, workload identity enabled, OIDC issuer)
- [ ] `storage.tf` — Azure Container Registry, Storage Account for state + ADLS
- [ ] `iam.tf` — Managed identity, federated credential (AKS OIDC → K8s service account)
- [ ] `database.tf` — Azure Database for PostgreSQL Flexible Server
- [ ] `outputs.tf` — AKS endpoint, ACR URL, PostgreSQL host, managed identity client ID

#### 1.3 Bootstrap: GCP (`bootstrap/gcp/`)
- [ ] `providers.tf` — google + google-beta providers, GCS backend
- [ ] `variables.tf` — project_id, region, gke_cluster_name, ar_repository_name, db vars
- [ ] `gke.tf` — GKE Autopilot cluster (workload identity enabled)
- [ ] `storage.tf` — GCS bucket for state, Artifact Registry repository
- [ ] `iam.tf` — Service account, workload identity binding (GKE SA → GCS)
- [ ] `database.tf` — Cloud SQL MySQL instance (db-f1-micro for dev)
- [ ] `outputs.tf` — GKE endpoint, AR URL, Cloud SQL host, service account email

#### 1.4 GitHub Actions: Multi-Cloud Workflow
- [ ] `.github/workflows/multi-cloud-pipeline.yml`
  - `cloud` input: `[aws, azure, gcp, all]`
  - `pipeline` input: `[eu_sales, us_crm, global_marketing, all]`
  - `bootstrap_cloud` input: `[skip, aws, azure, gcp]` + action `[apply, destroy]`
  - Conditional authentication blocks per cloud
  - Conditional kubeconfig per cloud

---

### Phase 2 — Azure Pipeline (US CRM)

**Goal:** End-to-end US CRM pipeline: PostgreSQL → ADLS Gen2 → Trino → Grafana on AKS.

- [ ] Run agent with `us_crm` pipeline on Azure bootstrap
- [ ] Validate Trino `azure_catalog` is queryable from AKS pod
- [ ] Grafana LoadBalancer accessible via AKS public IP
- [ ] Medic verifies "Deployment Complete" in AKS-deployed GitHub Actions workflow

**Key differences from AWS:**
- Auth: `azure/login` + workload identity federation (no static keys)
- Registry: ACR (`az acr login`) instead of ECR
- Kubeconfig: `az aks get-credentials`
- Trino connector: `hive` with `azure-storage` jar or `iceberg` with ABFS
- K8s Service Account: annotated with `azure.workload.identity/client-id`

---

### Phase 3 — GCP Pipeline (Global Marketing)

**Goal:** Hourly Marketing CronJob: MySQL → GCS → Trino → Grafana on GKE.

- [ ] Run agent with `global_marketing` pipeline on GCP bootstrap
- [ ] GKE CronJob runs hourly (`0 * * * *`)
- [ ] Trino `gcp_catalog` connected via GCS connector
- [ ] Validate incremental window logic (T-1 hour filter)

**Key differences from AWS:**
- Auth: `google-github-actions/auth@v2` with Workload Identity Federation
- Registry: Artifact Registry (`gcloud auth configure-docker`)
- Kubeconfig: `gcloud container clusters get-credentials`
- Trino connector: `hive` with `gcs` metastore or Iceberg on GCS
- K8s Service Account: annotated with `iam.gke.io/gcp-service-account`

---

### Phase 4 — Cross-Cloud Federation (Trino + Grafana)

**Goal:** Single Trino instance (or federated Trino mesh) querying S3 + ADLS + GCS simultaneously. Grafana dashboards with `cloud_provider` variable for filtering.

#### 4.1 Trino Multi-Catalog Configuration
Each Kubernetes cluster runs its own Trino, but the target is a **central Trino** that reads all 3:

```sql
-- Cross-cloud join example
SELECT 
    s.region,
    COUNT(s.sale_id)     AS sales_count,
    COUNT(c.customer_id) AS crm_contacts,
    SUM(m.impressions)   AS marketing_impressions
FROM hive.sales_eu.pipe_sales_eu_to_s3 s
LEFT JOIN azure_catalog.crm_us.pipe_crm_us_to_azure c
    ON s.customer_id = c.customer_id
LEFT JOIN gcp_catalog.marketing_global.pipe_mkt_global_to_gcp m
    ON s.campaign_id = m.campaign_id
WHERE s.run_date = CURRENT_DATE
GROUP BY 1
```

**Trino Catalog Files:**
- `hive.properties` — connector: hive, metastore: glue, region: eu-central-1
- `azure.properties` — connector: hive, metastore: file, fs: abfs
- `gcp.properties` — connector: hive, metastore: file, fs: gs

#### 4.2 Grafana Cross-Cloud Dashboard
- Template variable: `$cloud_provider` with values `aws | azure | gcp | all`
- Panel 1: Rows processed per cloud (grouped bar chart)
- Panel 2: Last success timestamp per pipeline (stat panel)
- Panel 3: Cross-cloud join preview (table panel via Trino datasource)
- Alert: Any pipeline silent > 60 minutes → PagerDuty/Slack

---

### Phase 5 — Production Hardening

- [ ] VPC peering / Private Link between cloud K8s and databases (remove public RDS access)
- [ ] Trino cluster autoscaling (Karpenter on EKS, KEDA on AKS/GKE)
- [ ] Pinecone `dynamic-experience` namespace: store self-healing fixes per cloud
- [ ] Multi-cloud chaos testing: `seed_chaos.py` targeting all 3 DBs simultaneously
- [ ] Cost tagging: all resources tagged with `project_id`, `cloud_provider`, `pipeline_id`
- [ ] SLA alerts: per-pipeline `pipeline_last_success_timestamp` > 90 min → critical

---

## GitHub Actions Inputs (Phase 1 Target)

| Input | Options | Description |
|---|---|---|
| `cloud` | `aws / azure / gcp / all` | Which cloud(s) to target |
| `pipeline` | `eu_sales / us_crm / global_marketing / all` | Which pipeline(s) to run |
| `bootstrap_cloud` | `skip / aws / azure / gcp` | Provision baseline infra |
| `bootstrap_action` | `apply / destroy` | Create or tear down |
| `chaos_target` | `skip / eu_sales / us_crm / global_marketing / all` | Dirty data injection |
| `db_type` | `postgres / mysql / sqlite` | Source DB type |
| `rows` | string (default: 100) | Number of chaos rows |
| `ingest_knowledge` | `yes / no` | Sync knowledge base to Pinecone |

---

## Environment Variables / Secrets (Full Map)

### AI & Observability
| Variable | Used for |
|---|---|
| `OPENAI_API_KEY` | LLM calls (GPT-4) |
| `PINECONE_API_KEY` | Vector store reads/writes |
| `PINECONE_INDEX_NAME` | Pinecone index |
| `LANGCHAIN_API_KEY` | LangSmith tracing |
| `GH_TOKEN` | GitHub API (log fetching, file push) |
| `GRAFANA_API_KEY` | Dashboard provisioning |
| `TRINO_USER` / `TRINO_PASSWORD` | Trino query auth |

### AWS
| Variable | Used for |
|---|---|
| `AWS_ACCESS_KEY_ID` | EC2/EKS/S3/ECR access |
| `AWS_SECRET_ACCESS_KEY` | EC2/EKS/S3/ECR access |
| `AWS_DEFAULT_REGION` | Region default |
| `BOOTSTRAP_AWS_ACCESS_KEY_ID` | Bootstrap-only IAM user |
| `BOOTSTRAP_AWS_SECRET_ACCESS_KEY` | Bootstrap-only IAM user |

### Azure
| Variable | Used for |
|---|---|
| `AZURE_CLIENT_ID` | Service principal (or managed identity) |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_TENANT_ID` | AAD tenant |
| `AZURE_SUBSCRIPTION_ID` | Subscription |
| `AZURE_CREDENTIALS` | JSON bundle for `azure/login` action |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob idempotency check |

### GCP
| Variable | Used for |
|---|---|
| `GCP_PROJECT_ID` | All GCP API calls |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | GitHub OIDC → GCP federation |
| `GCP_SERVICE_ACCOUNT` | Workload identity target SA |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to JSON key (fallback) |

### Databases
| Variable | Used for |
|---|---|
| `POSTGRES_DB_HOST/PORT/USER/PASSWORD/NAME` | AWS RDS (eu_sales) |
| `CRM_DB_HOST/PORT/USER/PASSWORD/NAME` | Azure PostgreSQL (us_crm) |
| `MYSQL_DB_HOST/PORT/USER/PASSWORD/NAME` | GCP Cloud SQL (global_marketing) |

---

## Key Design Decisions

### 1. One project folder, three clouds
Each pipeline YAML points to one `target_infra_config` (aws_s3.yaml / azure_blob.yaml / gcp_bucket.yaml). The agent detects the cloud from `cloud_provider` key and uses the matching bootstrap + auth module.

### 2. Agent prompts are cloud-agnostic
The Infra agent queries the knowledge base for the correct Terraform standard based on the cloud detected in context. It does NOT have AWS/Azure/GCP hardcoded in its system prompt.

### 3. Hive partitioning is universal
All three clouds write Parquet with `run_date=YYYY-MM-DD/` partitioning. Trino's Hive connector works the same way for S3, ABFS, and GCS — only the `external_location` URI prefix changes.

### 4. Bootstrap is per-cloud, pipelines are cloud-agnostic
Bootstrap (EKS/AKS/GKE) runs once per cloud. After that, any pipeline can target any cloud by changing `target_infra_config` in its YAML.

### 5. Trino is the federation layer
Instead of cloud-specific BI tools, a single Trino cluster (or per-cloud Trino instances with pass-through queries) federates all data. This makes Grafana truly cross-cloud with a single Prometheus datasource.

---

## Getting Started (Phase 1)

```bash
# 1. Install dependencies
make install

# 2. Bootstrap AWS infrastructure (one-time)
make bootstrap-aws

# 3. Sync knowledge base to Pinecone
make ingest

# 4. Seed dirty data
make chaos target=eu_sales db_type=postgres rows=200

# 5. Run the self-healing agent
make run p=eu_sales

# 6. (When Azure bootstrap is ready) bootstrap Azure
make bootstrap-azure

# 7. Run CRM pipeline on Azure
make run p=us_crm
```

Or trigger via GitHub Actions with cloud + pipeline selection inputs.
