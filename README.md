<p align="center">
  <img src="images/banner.png" alt="Multi-Agent · Multi-Cloud · Self-Healing" width="100%">
</p>

<h1 align="center">Self-Healing Multi-Cloud Data Pipeline Agents</h1>

<p align="center">
  <a href="https://github.com/theofanis-tsakanikas/self-healing-multi-cloud-agents/actions/workflows/tests.yml"><img src="https://github.com/theofanis-tsakanikas/self-healing-multi-cloud-agents/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/LangGraph-multi--agent-1C3C3C?logo=langgraph&logoColor=white" alt="Built with LangGraph">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/clouds-AWS%20%C2%B7%20Azure%20%C2%B7%20GCP%20%C2%B7%20Databricks-2E7D32" alt="Clouds: AWS, Azure, GCP, Databricks">
  <a href="evals/report/REPORT.md"><img src="https://img.shields.io/badge/self--heal%20evals-17%20cases%20%C2%B7%20100%25-brightgreen" alt="Self-heal evals: 17 cases, 100%"></a>
  <img src="https://img.shields.io/badge/model-gpt--4o--mini-lightgrey" alt="Model: gpt-4o-mini">
</p>

<p align="center"><b>Build. Deploy. Self-heal.</b></p>

An AI orchestration system that **designs, deploys, and repairs production data pipelines end-to-end** — Python ETL, SQL DDL, Terraform, Kubernetes, CI/CD, and observability dashboards — on **AWS, Azure, GCP, and Databricks**, from a single YAML config or a plain-English description.

When a deployment fails, the agent reads the real CI logs, diagnoses the error with quoted evidence, patches the exact file, and redeploys — **without a human in the loop**. And when it *can't* fix something, it stops, fails closed, and hands you the exact diagnosis.

> Runs on **gpt-4o-mini**. The reliability lives in the architecture — deterministic routing, evidence gates, golden-tested code generation — not in model size.

---

## Contents

- [It fixes itself — here is the proof](#it-fixes-itself--here-is-the-proof)
  - [It heals at runtime too](#it-heals-at-runtime-too--not-just-at-generation-time) · […and when it can't, it stops](#and-when-it-cant-fix-it-it-stops)
- [Validated end-to-end — on all four clouds](#validated-end-to-end--on-all-four-clouds)
- [How it works](#how-it-works)
  - [The self-healing loop](#the-self-healing-loop) · [Where the LLM works — and where it must not](#where-the-llm-works--and-where-it-must-not) · [Standards-first generation (RAG)](#standards-first-generation-rag)
- [Cloud-agnostic by construction](#cloud-agnostic-by-construction)
- [Observability](#observability--provisioned-by-the-agent-not-by-hand)
- [Natural-language authoring](#natural-language-authoring)
- [From config to running infrastructure](#from-config-to-running-infrastructure)
- [Repository map — source vs. generated](#repository-map--source-vs-generated)
- [Quickstart](#quickstart) · [Testing](#testing)
- [Engineering decisions a reviewer should notice](#engineering-decisions-a-reviewer-should-notice)
- [Documentation](#documentation) · [License](#license)

---

## It fixes itself — here is the proof

A generated pipeline script failed validation. Nobody touched it. The Medic diagnosed it, routed it to the Architect, patched the exact line, and re-validated — in **22 seconds**.

<table>
<tr>
<td width="50%"><img src="images/gcp-architect-validation-failed.png" alt="Validation failed, routing to Medic"><br><sub><b>1 · Break</b> — <code>AUTO-VALIDATION FAILED</code> → <i>“Architect explicit error flag set. Routing to MEDIC.”</i></sub></td>
<td width="50%"><img src="images/gcp-architect-heal-passed.png" alt="Validation passed after patch"><br><sub><b>2 · Heal</b> — <i>“Medic requested Logic fix”</i> → Fix mode → <code>AUTO-VALIDATION PASSED after patch</code> → routing to INFRA</sub></td>
</tr>
</table>

The diagnosis is not a guess. Here is the same heal inside LangSmith — the error, the Medic's fix request, the **exact one-line patch**, and the re-validation:

<table>
<tr>
<td width="50%"><img src="images/langsmith_gcp_validate_error.png" alt="Validation failed on the generated script"><br><sub><b>The error</b> — <code>validate_generated_code</code> → <code>VALIDATION FAILED</code>: a temporal comparison against <code>pd.Timestamp.now()</code> on a string column with no <code>pd.to_datetime(..., errors='coerce')</code> first, which raises <i>Invalid comparison between dtype=str and Timestamp</i> at runtime</sub></td>
<td width="50%"><img src="images/langsmith_gcp_request_fix.png" alt="Medic request_fix with evidence"><br><sub><b>The diagnosis</b> — <code>request_fix</code> with a verbatim <code>evidence_quote</code>, a concrete <code>suggested_fix</code>, and <code>target_agent: architect</code></sub></td>
</tr>
<tr>
<td width="50%"><img src="images/langsmith_gcp_patch.png" alt="Surgical patch applied"><br><sub><b>The patch</b> — <code>patch_project_file</code> with a surgical <code>old</code> → <code>new</code> replacement of the exact line. <code>PATCH APPLIED … replaced (1x)</code></sub></td>
<td width="50%"><img src="images/langsmith_gcp_validate_ok.png" alt="Re-validation clean"><br><sub><b>The proof</b> — <code>validate_generated_code</code> → <code>CLEAN: … passed all validation checks</code></sub></td>
</tr>
</table>

### It heals at runtime too — not just at generation time

On Databricks the Spark job fails **while running on the cluster**. The Medic reads the job's own output from CI, routes it to Infra (the script was correct — the Terraform was wrong), patches the secret key, re-applies, and re-runs the job.

<table>
<tr>
<td width="50%"><img src="images/databricks-job-secret-error.png" alt="Databricks job failed at runtime"><br><sub><b>Runtime failure</b> — <code>Secret does not exist with scope: … key: db_password</code></sub></td>
<td width="50%"><img src="images/langsmith-dbx-patch-secret-key.png" alt="Terraform secret key patched"><br><sub><b>Infra heal</b> — <code>key = "postgres_password"</code> → <code>key = "db_password"</code>, then <code>execute_terraform</code> + push</sub></td>
</tr>
</table>

<p align="center">
  <img src="images/databricks-job-succeeded.png" alt="Databricks job succeeded after heal" width="85%"><br>
  <sub>The re-run after the heal — <b>Succeeded</b>.</sub>
</p>

### …and when it can't fix it, it stops

Every AI-agent demo ends green. This one is allowed to end **red** — because a system that pretends to succeed is worse than one that admits it failed.

```
Fix loop not converging: the same error survived 3 attempts.
❌ MISSION FAILED: mission_status='escalated' — self-healing was abandoned.
   See the Medic's last message for the exact diagnosis.
```

The heal is **bounded** (3 identical errors, or 8 total rounds) and **fail-closed**: `mission_status = "verified"` is the only success. Anything else exits non-zero, turns CI red, and surfaces the diagnosis to a human.

---

## Validated end-to-end — on all four clouds

Every row is a real pipeline that ran to completion on live cloud infrastructure — chaos-seeded dirty source data in, partitioned clean data + populated dashboards out — then torn down to zero cost.

| Cloud | Pipeline | Source → Destination | Compute | Observability | Validated |
|---|---|---|---|---|---|
| 🟠 **AWS** | `eu_sales` | RDS PostgreSQL → S3 (Parquet) | EKS | Trino + Glue · Grafana | ✅ deploy + self-heal |
| 🔵 **Azure** | `us_crm` | Azure PostgreSQL → ADLS Gen2 | AKS | Trino · Grafana | ✅ deploy + self-heal |
| 🟢 **GCP** | `global_marketing` | Cloud SQL MySQL → GCS | GKE | Trino · Grafana | ✅ deploy + self-heal |
| ⚡ **Databricks** | `sales_lakehouse` | RDS PostgreSQL → Delta Lake | Spark (jobs cluster) | Unity Catalog · Lakeview | ✅ deploy + self-heal |

> The self-healing loop is **one code path shared by every cloud** — the same router, the same evidence gate, the same patch mechanism. Which *kind* of failure each run happened to hit (a bad script, a bad Terraform value, a failing Spark job) differs only because a different defect was injected; it is not a per-cloud capability.

The three object-storage clouds share one cloud-agnostic execution model (pandas → Parquet → Trino → Grafana on Kubernetes). Databricks is a deliberately distinct fourth model (Spark → Delta → Unity Catalog → Lakeview) selected by the same `provider:` switch — proving the architecture generalizes across genuinely different platforms, not just across vendor APIs.

**One code path, three cloud APIs.** The same deterministic routing fix healed an invalid Terraform value on all three object-storage clouds — three different providers, three different error formats, zero cloud-specific code:

| AWS | Azure | GCP |
|---|---|---|
| `expected …status to be one of [Enabled Disabled Suspended]` | `expected account_tier to be one of ["Premium" "Standard"]` | `googleapi: Error 400: Invalid storage class "STD"` |
| `On` → `Enabled` | `Std` → `Standard` | `STD` → `STANDARD` |

<p align="center">
  <img src="images/aws_run_9m17s_cropped.png" alt="MISSION VERIFIED" width="90%"><br>
  <sub><code>✅ MISSION VERIFIED — deployment completed and validated end-to-end</code>, with the full agent routing trace above it.</sub>
</p>

---

## How it works

```mermaid
flowchart TD
    U["Pipeline YAML<br/>or plain-English description"] --> S
    subgraph G["LangGraph state machine"]
        S["🧭 Supervisor<br/>deterministic router"]
        A["📐 Architect<br/>ETL code · SQL DDL"]
        I["🏗️ Infra<br/>Terraform · artifact push"]
        M["🩺 Medic<br/>evidence-grounded diagnosis"]
        S --> A --> S
        S --> I --> S
        S --> M
        M -- "request_fix(file, evidence_quote)" --> S
    end
    KB[("Pinecone<br/>engineering standards")] -. RAG .-> A & I & M
    I -- "git push" --> GH["GitHub Actions"]
    GH -- "build · terraform · deploy" --> C["AWS · Azure · GCP · Databricks"]
    GH -- "CI logs" --> M
```

Hub-and-spoke: the Supervisor routes to Architect / Infra / Medic and each returns to it. The Supervisor is the **mission-outcome node** — it turns green only when the deployment is verified end-to-end.

<table>
<tr>
<td width="50%"><img src="images/streamlit-agent-graph-tight.png" alt="Agent graph mid-run"><br><sub><b>Mid-run</b> — Architect ✓, Medic ✓, Infra active</sub></td>
<td width="50%"><img src="images/streamlit-agent-graph-final.png" alt="Agent graph all complete"><br><sub><b>Mission complete</b> — every node green, 21 supervisor turns</sub></td>
</tr>
</table>

**Supervisor** — a deterministic router (the LLM emits exactly one word). Routing invariants live in Python, not in prompt prose.

**Architect** — generates the two judgment artifacts: the pipeline implementation (chunked ETL with real pandas business-rule logic — no stub `is_suspicious = False` — idempotency checks, typed casts, per-rule rejection metrics) and the Trino/Unity Catalog DDL with schema-derived columns. Phase-gated: Discovery → Schema → Implementation, one tool per phase.

**Infra** — generates the per-cloud pipeline Terraform, then pushes all artifacts and triggers CI. The Kubernetes manifests, Dockerfile and deploy workflow are rendered deterministically from config.

**Medic** — watches the CI run with exponential-backoff polling, then diagnoses failures from a **structured validation summary built in Python** — never by free-form log "interpretation". Its `request_fix` tool *rejects* any diagnosis whose evidence quote is not present verbatim in a real tool/log output (a provenance check), so a hallucinated fix has nothing to route to. Fixes are surgical patches to the named file only; clean files are off-limits.

### The self-healing loop

```mermaid
sequenceDiagram
    participant CI as GitHub Actions
    participant M as 🩺 Medic
    participant S as 🧭 Supervisor
    participant O as Owning agent<br/>(Architect / Infra)

    CI-->>M: failing logs (poll w/ backoff)
    M->>M: parse to structured summary (Python)
    M->>M: evidence gate — quote must exist verbatim
    alt evidence is real
        M->>S: request_fix(file, evidence, owner)
        S->>O: one-shot healing_context
        O->>O: patch_project_file (surgical) + auto-validate
        O->>CI: re-push → redeploy
        CI-->>M: re-poll
        M-->>S: mission_status = "verified" ✅
    else no convergence (3 same / 8 total)
        M-->>S: mission_status = "escalated" ❌
        S-->>CI: exit 1 — red, with the diagnosis
    end
```

### Where the LLM works — and where it must not

Every artifact was scored by **input variability**. Where the input is open, the LLM works. Where there is exactly one correct answer, deterministic code renders it from config and a golden test pins it.

```mermaid
flowchart LR
    subgraph LLM["🧠 LLM-owned — judgment under variability"]
        L1["Pipeline script<br/>pandas / PySpark"]
        L2["SQL DDL<br/>Trino / Unity Catalog"]
        L3["Pipeline Terraform"]
        L4["Diagnosis"]
    end
    subgraph CODE["⚙️ Code-owned — agents/codegen.py, golden-tested"]
        C1["requirements.txt"]
        C2["Dockerfile"]
        C3["6× K8s manifests"]
        C4["Deploy workflow ×4 clouds"]
        C5["Dashboards"]
    end
    V["validate_generated_code<br/>policy safety net"] --> LLM
    V --> CODE
```

The LLM was *removed* from everything it used to merely copy. An agent that knows where **not** to use the LLM is the difference between an AI system and an expensive template engine.

### Standards-first generation (RAG)

Agents don't improvise conventions — they retrieve versioned engineering standards covering Terraform, K8s, Spark/Delta, CI/CD, SQL, and dashboards. When output is wrong, the **standard** gets fixed, never the generated file.

<table>
<tr>
<td width="50%"><img src="images/pinecone-knowledge-base.png" alt="Pinecone knowledge base"><br><sub>The standards corpus in Pinecone — one vector per standard</sub></td>
<td width="50%"><img src="images/langsmith-rag-retrieval.png" alt="RAG retrieval"><br><sub>Infra retrieving the GCP Terraform standard at generation time</sub></td>
</tr>
</table>

---

## Cloud-agnostic by construction

The same agent, the same config shape, three different infrastructure APIs — generated per cloud, never hardcoded:

<table>
<tr>
<td width="33%"><img src="images/langsmith-infra-terraform.png" alt="AWS Terraform"><br><sub>🟠 <b>AWS</b> — S3 + public-access block + KMS + lifecycle</sub></td>
<td width="33%"><img src="images/langsmith-azure-terraform.png" alt="Azure Terraform"><br><sub>🔵 <b>Azure</b> — ADLS Gen2 container + managed-identity role</sub></td>
<td width="33%"><img src="images/langsmith-gcp-terraform.png" alt="GCP Terraform"><br><sub>🟢 <b>GCP</b> — GCS bucket + IAM member + Workload Identity</sub></td>
</tr>
</table>

**Real data landed, on every cloud** — partitioned by `run_date`, written by the generated pipeline:

<table>
<tr>
<td width="33%"><img src="images/aws-s3-output-parquet.png" alt="AWS S3 output"><br><sub>🟠 S3 — <code>run_date=…/part_0.parquet</code></sub></td>
<td width="33%"><img src="images/azure-storage-output-parquet.png" alt="Azure ADLS output"><br><sub>🔵 ADLS Gen2 — same layout</sub></td>
<td width="33%"><img src="images/gcp-storage-output-parquet.png" alt="GCS output"><br><sub>🟢 GCS — same layout</sub></td>
</tr>
</table>

---

## Observability — provisioned by the agent, not by hand

Each object-storage pipeline ships with a Grafana dashboard (record count, rejection rate, run duration, per-rule rejection breakdown) fed by Prometheus Pushgateway gauges:

<table>
<tr>
<td width="33%"><img src="images/grafana-eu-sales-dashboard.png" alt="AWS Grafana"><br><sub>🟠 <b>AWS</b> — 66 records · 34% rejected · 394 ms</sub></td>
<td width="33%"><img src="images/grafana-crm-dashboard.png" alt="Azure Grafana"><br><sub>🔵 <b>Azure</b> — 80 records · 20% rejected · 317 ms</sub></td>
<td width="33%"><img src="images/grafana-marketing-dashboard.png" alt="GCP Grafana"><br><sub>🟢 <b>GCP</b> — 76 records · 24% rejected · 1.06 s</sub></td>
</tr>
</table>

Databricks ships the native equivalent — a **Lakeview** dashboard over a per-run `_audit` Delta table in Unity Catalog:

<table>
<tr>
<td width="50%"><img src="images/databricks-lakeview-dashboard.png" alt="Lakeview dashboard — counters"><br><sub><b>Top</b> — rows processed, rejected, run duration, last run</sub></td>
<td width="50%"><img src="images/dbx_dashboard.png" alt="Lakeview dashboard — rejections by reason"><br><sub><b>Bottom</b> — rejections by reason and rows over time</sub></td>
</tr>
</table>

<p align="center">
  <img src="images/databricks-unity-catalog-table.png" alt="Unity Catalog Delta table" width="90%"><br>
  <sub>The Delta table in Unity Catalog — cleaned, typed, partitioned by <code>run_date</code>.</sub>
</p>

---

## Natural-language authoring

Describe a pipeline in plain English → the system extracts a typed intent, fills the gaps, proposes business rules, **prices the build on all four platforms**, and deploys on confirmation.

<table>
<tr>
<td width="50%"><img src="images/streamlit-nl-describe.png" alt="Describe in plain English"><br><sub><b>1 · Describe</b> — free text in</sub></td>
<td width="50%"><img src="images/streamlit-fields-review.png" alt="Review extracted fields"><br><sub><b>2 · Fields</b> — extracted, typed, editable</sub></td>
</tr>
<tr>
<td width="50%"><img src="images/streamlit-business-rules.png" alt="Business rules"><br><sub><b>3 · Rules</b> — plain-language quality rules → real pandas conditions</sub></td>
<td width="50%"><img src="images/streamlit-cost-comparison-full-tight.png" alt="Cost comparison"><br><sub><b>4 · Price it</b> — itemized monthly cost, all four platforms</sub></td>
</tr>
</table>

Rules can come from a file, be suggested for your domain — or you can **build them yourself**, column by column, with the action and the generated condition shown as you go:

<table>
<tr>
<td width="50%"><img src="images/build_my_rules.png" alt="Build your own rule"><br><sub>Pick the columns, the check and the failure action</sub></td>
<td width="50%"><img src="images/build_my_rules1.png" alt="Rule added to the set"><br><sub>The rule joins the set — editable, removable, ready to generate</sub></td>
</tr>
</table>

Nothing deploys before you see exactly what will be created:

<p align="center">
  <img src="images/streamlit-execution-plan.png" alt="Execution plan" width="90%"><br>
  <sub><b>5 · Execution plan</b> — pipeline code, catalog, dashboards, Kubernetes, Terraform, CI/CD — then <i>Confirm &amp; Deploy</i>.</sub>
</p>

---

## From config to running infrastructure

<table>
<tr>
<td width="50%"><img src="images/architect-codegen-validation.png" alt="Architect writes and validates"><br><sub><b>Architect</b> — schema discovery → codegen → <code>AUTO-VALIDATION PASSED</code> → hand off to Infra</sub></td>
<td width="50%"><img src="images/kubernetes-deploy-complete.png" alt="Kubernetes deployment complete"><br><sub><b>Deploy</b> — Trino, Grafana, Prometheus + the pipeline Job, all <code>Running</code></sub></td>
</tr>
</table>

---

## Repository map — source vs. generated

A defining property of this repo: **part of it is the agent, part of it is the agent's output**, committed as evidence of what a run produces.

| Path | What it is |
|---|---|
| `agents/`, `graph.py`, `main.py` | The multi-agent system (LangGraph nodes, tools, prompts) |
| `knowledge_base/` | Versioned engineering standards (RAG corpus) |
| `configs/` | Pipeline definitions: objective, source DB, business rules, target infra |
| `bootstrap/` | One-time per-cloud baseline Terraform (EKS/AKS/GKE/Databricks workspace, registries, source DBs, state) |
| `utils/`, `tests/` | Shared libraries + hermetic unit tests (no cloud, no credentials) |
| `evals/` | Offline self-heal eval harness (failure corpus, replay/eval modes, `heal` CLI) |
| `streamlit_app.py`, `utils/nlp_parser.py`, `utils/cost_estimator.py` | NL authoring demo: free text → pipeline config, with a 4-cloud cost comparison before deploy |
| **Generated:** `scripts/pipe_*.py`, `k8s/`, `sql/`, `dashboards/`, `terraform/`, `Dockerfile`, `requirements.txt`, `.github/workflows/*_pipeline.yml` | **Agent outputs** — populated by each run, fixed only through standards/prompts, never edited by hand |

> The generated paths are empty between runs — each run commits a fresh, coherent set for its target cloud. The complete artifact set from the validated runs is preserved at the [`v1.0.0` tag](https://github.com/theofanis-tsakanikas/self-healing-multi-cloud-agents/tree/v1.0.0).

---

## Quickstart

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Terraform ≥ 1.6, an OpenAI (or Anthropic / Vertex AI) key, a Pinecone index, and credentials for at least one cloud.

```bash
# 1. Configure
cp .env.example .env          # fill in keys — .env.example documents every variable

# 2. Install
make install                  # uv sync

# 3. One-time cloud baseline (pick your cloud)
make bootstrap-aws            # EKS + RDS + S3 + ECR + SSM

# 4. Sync the engineering standards to Pinecone
make ingest

# 5. Full demo: seed 100 rows of dirty data, then let the agent build & heal
make demo-aws                 # = ingest + chaos + run p=eu_sales
```

Run any pipeline directly:

```bash
make run p=eu_sales           # AWS
make run p=us_crm             # Azure
make run p=global_marketing   # GCP
make run p=sales_lakehouse    # Databricks
```

Everything is also operable from GitHub Actions (`run_agent.yml` — `workflow_dispatch` with bootstrap / chaos / KB-sync / pipeline inputs), and every cloud tears down with one button (`destroy.yml`, typed confirmation).

<table>
<tr>
<td width="50%"><img src="images/github-run-workflow-trigger.png" alt="Pick the pipeline"><br><sub><b>Pick the pipeline</b> — <code>eu_sales</code>, <code>us_crm</code>, <code>global_marketing</code> or <code>sales_lakehouse</code></sub></td>
<td width="50%"><img src="images/gh_kb_sync.png" alt="Sync knowledge base and seed chaos"><br><sub><b>Same dispatch</b> — re-sync the standards to Pinecone and seed dirty rows to prove the healing</sub></td>
</tr>
</table>

### Natural-language authoring (demo UI)

```bash
uv run streamlit run streamlit_app.py
```

---

## Testing

```bash
make test         # hermetic unit tests — no cloud, no credentials, every external dependency mocked
make eval-replay  # offline Medic self-heal eval — score the failure corpus, no LLM/cloud/keys
make heal LOG=run.log   # route + validate ANY failing CI log through the real Medic logic (offline)
```

CI (`tests.yml`) runs lint + the suite with a coverage floor on every push/PR. The deterministic core (routing, validators, state lifecycle, credential resolution) is unit-tested.

**Self-heal eval harness ([`evals/`](evals/), [docs/EVAL_HARNESS.md](docs/EVAL_HARNESS.md)).** The Medic's judgment — diagnosing a failed CI run and routing an evidence-grounded fix — is measured offline against a corpus of the documented failure classes. **Replay mode** (`make eval-replay`, gated in CI) scores the *real* routing + anti-hallucination evidence gate with **no LLM, no cloud, no keys** — 17 failure classes, routing and evidence gate at 100%. **Eval mode** (`make eval-live`) scores the current model's diagnosis quality, catching model regressions. The `heal` CLI runs the same judgment on any failing log, decoupled from the pipelines this agent generated.

---

## Engineering decisions a reviewer should notice

- **Standards-first generation.** Conventions are retrieved, versioned artifacts — not prompt folklore. Wrong output means a wrong *standard*, and that is what gets fixed.
- **Deterministic where it counts.** Tool sequencing (`tool_choice="required"`, pre-computed args), owner routing, validation gates, and guaranteed injections are Python — not "prompt harder and hope".
- **Anti-hallucination by construction.** A fix request without a verbatim quote from real output is refused. Green runs and clean files cannot be "fixed".
- **Bounded autonomy.** The heal loop has a hard stop and a fail-closed terminal contract. Unverified is never success.
- **Validation as a safety net, not a crutch.** `validate_generated_code` enforces policy (credential access only via the sanctioned resolver, no hardcoded regions, no template literals in K8s manifests) before anything reaches CI.
- **Cloud-agnostic by construction.** No default cloud anywhere — the provider is always read from config.

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — as-built architecture: agents, state machine, per-cloud execution models, credential resolution
- [docs/EVAL_HARNESS.md](docs/EVAL_HARNESS.md) — the offline replay + eval harness for the self-healing Medic
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — verification steps, failure signatures, teardown procedures
- [docs/VISION.md](docs/VISION.md) — product vision and roadmap
- [SECURITY.md](SECURITY.md) — what is hardened, the deliberate demo trade-offs, and the production posture for each
- [CHANGELOG.md](CHANGELOG.md) — release history
- [CONTRIBUTING.md](CONTRIBUTING.md) — local setup, dependency management
- [CLAUDE.md](CLAUDE.md) — the project's full engineering rulebook (also used by AI coding assistants)

## License

[MIT](LICENSE) © 2026 Theofanis Tsakanikas
