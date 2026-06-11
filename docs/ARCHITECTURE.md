# Architecture — As Built

> This document describes the system **as it runs today**, validated end-to-end on all four platforms (AWS · Azure · GCP · Databricks). For the forward-looking roadmap see [VISION.md](VISION.md); for the full engineering rulebook see [CLAUDE.md](../CLAUDE.md).

---

## 1. The agent graph

A LangGraph state machine with four nodes sharing one `AgentState`:

```
SUPERVISOR  ── deterministic router (LLM emits ONE word; invariants live in Python)
    ├── ARCHITECT   pipeline script · SQL DDL · dashboard spec · (fix-mode: surgical patches)
    ├── INFRA       Terraform · K8s manifests · Dockerfile · deploy workflow · git push
    └── MEDIC       CI polling · evidence-grounded diagnosis · request_fix handoffs
```

- **Architect** and **Infra** always return to the Supervisor.
- **Medic** loops through a ToolNode (`fetch_github_action_logs`, `query_vector_store`, `request_fix`, `store_architectural_insight`); a successful `request_fix` short-circuits straight back to the Supervisor for a deterministic handoff — no extra LLM turn, no tool loops.
- Entry point and all routing decisions are conditional edges on `state["next_step"]` (`graph.py`).

### Phase-gated tool control

Agents do not freely choose tools. Each turn is a gated phase with exactly one permitted tool, `tool_choice="required"`, and pre-computed arguments:

- **Architect:** Discovery → Schema → Implementation (one artifact per implementation step).
- **Medic:** two tools, with `request_fix` requiring a verbatim `evidence_quote` from real logs — the tool **rejects** calls whose quote contains no recognized error marker.
- **Supervisor:** no tools at all.

This is the first of two control axes: *which tool, when* is orchestration and belongs to Python. *What the generated code says* belongs to the LLM — within validated bounds (see §5).

---

## 2. Two execution models, four providers

The provider is always read from `cloud_provider` / `provider:` in config. **No cloud is the default anywhere.**

### Object-storage model (AWS · Azure · GCP)

```
Source DB ──pandas (chunked)──▶ Parquet in object storage (Hive-partitioned by run_date)
                                      │
                              Trino (per-cloud, in-cluster) ── external tables + partition sync
                                      │
                       Grafana + Prometheus/Pushgateway (per-cloud, in-cluster)
```

| | AWS | Azure | GCP |
|---|---|---|---|
| Compute | EKS | AKS | GKE |
| Storage | S3 (`s3://`) | ADLS Gen2 (`abfss://`) | GCS (`gs://`) |
| Registry | ECR | ACR | Artifact Registry |
| Source DB | RDS PostgreSQL | Azure PostgreSQL Flexible | Cloud SQL MySQL |
| Catalog | Glue (via Trino `hive`) | Trino `hive` | Trino `hive` |
| Image tag in `job.yaml` | `:${{ github.sha }}` via CI `sed` | `:${{ github.sha }}` via CI `sed` | `:latest` (no sed — AR tag quirk) |

Generated pipeline scripts keep the **full three-cloud skeleton** (all `if _CLOUD == ...` branches with real bodies); only the active branch executes. This is the proven-stable form — collapsing to one branch measurably increased generation variance.

### Lakehouse model (Databricks)

```
Source RDS ──Spark JDBC──▶ Delta table (Unity Catalog) + per-run _audit Delta table
                                      │
                    Lakeview (AI/BI) dashboard on a serverless SQL warehouse
```

No Docker, no K8s, no Grafana/Prometheus, no `requirements.txt` — the jobs-cluster runtime provides Spark/Delta and the JDBC driver is a Maven library on the job. Artifacts: the Spark script, Unity Catalog DDL, the Lakeview dashboard JSON, and five pipeline Terraform files (secret scope, `databricks_job`, `databricks_dashboard`). Deploy authenticates **as the service principal** (`oauth-m2m`) so the SP can self-bind into the job's `run_as`.

> When a rule says "all clouds", it must be checked against both models: Databricks is a peer provider but a distinct execution model.

---

## 3. Credential resolution

`cloud_get()` (`utils/cloud_config.py`) is the **only** sanctioned way for a generated object-storage script to read DB credentials — `os.getenv()` for DB variables is a policy violation caught by the validator.

| Provider | Resolution path |
|---|---|
| AWS | 3-tier: SSM Parameter Store (via IRSA) → `.bootstrap_outputs.json` → env |
| GCP / Azure | env vars (populated into the K8s secret from GitHub vars/secrets) |
| Databricks | **not** `cloud_get()` — password via `dbutils.secrets.get(scope, ...)` (secret scope fed from SSM by the pipeline Terraform); host/name/user as job parameters |

Infrastructure values follow the same discipline: e.g. the registry URL has a single source of truth (SSM, written by bootstrap) read back via `cloud_get_infra()` — never parsed from Terraform output or self-assembled.

---

## 4. The self-healing loop

1. CI deploy fails (chaos-seeded data, generated-code defect, or infra error).
2. **Medic** polls the run with exponential backoff (30s → 300s, 5 attempts max, then escalates to the user — never loops to the recursion limit).
3. Python — not the LLM — parses the message history into a structured **VALIDATION SUMMARY** (FAILED files with verbatim error text + CLEAN files). The LLM never "discovers" errors by scanning raw logs.
4. `request_fix(file, evidence_quote)` is rejected unless the quote contains a real error marker; only FAILED files may be named.
5. The fix routes as a **one-shot `healing_context`** to the owning agent — Architect for code, Infra for infra — and is cleared by whoever consumes it. Multiple fixes in one turn accumulate.
6. The Architect in fix mode uses `patch_project_file` (surgical, auto-validated) — never a full rewrite.
7. Push, re-poll, repeat until green.

Special cases are handled deterministically: a 403 on log fetch is reported as a token-permissions problem (not routed as a code fix); `medic_fix_requested` distinguishes "infra must redo Terraform" from "skip straight to push".

---

## 5. The LLM-vs-deterministic boundary

The single most important design decision, kept deliberate rather than accidental:

- **LLM owns judgment under variability:** arbitrary source schemas, natural-language business rules → real pandas/Spark logic, SQL DDL, Terraform, log diagnosis.
- **Code owns everything mechanically determined** (`agents/codegen.py`, golden-tested against the validated v1.0.0 artifacts): `requirements.txt`, the Grafana dashboard JSON, the Lakeview dashboard, the Dockerfile, all six K8s manifests, and the per-cloud deploy workflow are rendered deterministically from config and pass the same validation gate — the LLM never emits them. Their standards remain in the knowledge base as the generators' spec and the Medic's diagnostic reference.
- **Plus guaranteed injections** on the LLM-owned artifacts: when a correct output is provably required yet generation drops it intermittently, it is injected at write time (cloud-SDK imports, f-string brace repair).
- **Anti-pattern actively avoided:** an LLM step for a fully deterministic artifact with repair code underneath — that is exactly the migration that produced `codegen.py`.

Supporting layers:

- **Knowledge base (`knowledge_base/*.md`)** — versioned engineering standards (Terraform per cloud, K8s, Spark/Delta, CI/CD, SQL, dashboards, Python) served to agents via Pinecone RAG (full-document, no chunking). When generated output is wrong, **the standard is fixed, never the output** — prompts state *what and when*, standards own *how and why*.
- **Validator (`validate_generated_code`)** — a safety net for policy and architecture-specific rules (credential access, no literal regions, no `${{ ... }}` inside K8s manifests, dashboard template-variable enforcement), not a linter for general style.

---

## 6. CI/CD

- **`run_agent.yml`** (manual dispatch) — operates the agent itself: optional per-cloud bootstrap, knowledge-base sync to Pinecone, chaos injection, and the pipeline run.
- **Generated `<pipeline>_pipeline.yml`** — the deploy workflow the Infra agent writes: build & push image (object-storage clouds) or DBFS upload (Databricks), pipeline Terraform, deploy, verify. Triggered by pushes scoped to **artifact paths only** — editing agents/standards never redeploys a pipeline.
- **`tests.yml`** — hermetic unit tests + lint + coverage floor on every push/PR; no cloud, no credentials.
- **`cleanup_k8s.yml` / `destroy.yml` / `power.yml`** — workload teardown, full per-cloud destroy, and pause/resume cost controls.
- Secrets vs variables follow a strict split: credentials in GitHub **Secrets**, non-sensitive config (regions, DB hosts/users) in **Variables** — generated workflows never contain literals for either.

---

## 7. Repository zones

| Zone | Paths | Change policy |
|---|---|---|
| Agent system | `agents/`, `graph.py`, `main.py`, `utils/` | Normal code review + unit tests |
| Standards (RAG corpus) | `knowledge_base/` | Edit + **re-sync to Pinecone** (`sync_knowledge_base: sync`) |
| Pipeline definitions | `configs/` | Declarative inputs per pipeline |
| One-time baseline | `bootstrap/<cloud>/` | Hand-written Terraform, applied once per cloud |
| **Generated outputs** | `scripts/`, `k8s/`, `sql/`, `dashboards/`, `terraform/`, `Dockerfile`, `requirements.txt`, `.github/workflows/*_pipeline.yml` | **Never edited by hand** — fixed via standards/prompts, regenerated by the next run |
| Demo surface | `streamlit_app.py`, `utils/nlp_parser.py`, `utils/cost_estimator.py` | NL authoring + cost preview; isolated from the agent generation path |
