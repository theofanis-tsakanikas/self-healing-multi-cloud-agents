# CLAUDE.md — Multi-Cloud Self-Healing Data Engineer Agent

## Project Identity

Production-grade AI orchestration system that autonomously designs, deploys, and self-heals data pipelines across AWS, Azure, GCP, and Databricks using a LangGraph multi-agent architecture: **Supervisor → Architect → Infra → Medic**.

Key docs: `docs/ARCHITECTURE.md` (as-built), `docs/RUNBOOK.md` (verification/ops), `SECURITY.md` (posture + demo trade-offs).

## Commands

```bash
make install                  # uv sync
make test                     # 156+ hermetic unit tests — no cloud, no credentials
make lint                     # ruff, same command CI runs
make run p=<pipeline>         # eu_sales | us_crm | global_marketing | sales_lakehouse
make ingest                   # sync knowledge_base/ -> Pinecone (REQUIRED after editing any standard)
make chaos target=<p> db_type=<t> rows=N
```

---

## Non-Negotiable Principles

- **Cloud Agnostic:** AWS, GCP, Azure are equals (object-storage + Kubernetes model). No cloud is the default. Cloud is always read from `cloud_provider` in config — never assumed. **Databricks** is a 4th provider with a *distinct execution model* — Spark + Delta + Unity Catalog instead of pandas/parquet/Trino/K8s — selected the same way (`provider: databricks`) but driven by its own standards (`databricks_spark_standard.md`, `terraform_databricks.md`). When a rule says "all clouds", check whether it means the three object-storage clouds or genuinely includes Databricks.
- **No Shortcuts:** Production solution always. No TODOs, no placeholders, no "simplified for now".
- **Standards First:** When the LLM generates wrong output, fix the **standard or prompt** — not the generated file. Hardcoded one-off fixes are never the answer.
- **Deterministic generation guarantees:** when a correct output is *mechanically determined* (not a judgement call) yet the LLM still drops it intermittently despite the prompt, resolve it in **Python at generation time** — a guarantee, not a one-off patch. Established cases: `write_project_file` auto-injects the cloud-SDK import the script's SDK call requires (F821); `write_project_file` un-doubles f-string braces the model double-braced (`{{…}}` → `{…}`) in a Databricks Spark script (F541 + a corrupted `replaceWhere`); `write_project_file` rebuilds the Databricks Lakeview dashboard JSON from the canonical structure (`_canonical_lakeview_dashboard`) — the LLM reliably substitutes the `_audit` table name but intermittently mangles the nested widget encodings (e.g. nests `color` inside `y.scale`) into invalid JSON, so only the table name is taken from the LLM and the structure is regenerated; `write_project_file` drops `requirements.txt` on Databricks pipelines (detected via the `PIPELINE_PLATFORM` env var set in `main.py` from `target_infra_config.provider`; the LLM emits it empty or pyspark-only and the cluster runtime provides pyspark — `validate_generated_code` likewise never blocks a databricks run on it, with a pyspark-only-content fallback for entry points that don't set the env var); `generate_k8s_manifest` fills `__EMBED_SETUP_TRINO_SQL__` / `__EMBED_MONITORING_SPECS_JSON__` verbatim from disk (truncation/transcription); `infra.py` pre-resolves the single per-cloud iac query (no wrong-cloud query). This is distinct from "patching the output" — it injects what is provably required, every time.
- **LLM vs deterministic code — keep the boundary deliberate (not "wherever the LLM happened to fail"):** there are TWO axes of control; keep them distinct.
  - **(A) Orchestration gating** — *which* tool, *when*. A phase-gated state machine: one tool per phase, `tool_choice="required"`, pre-computed args (architect Discovery→Schema→Implementation; medic has 2 tools; the supervisor emits ONE WORD, no tools). This is good architecture — **keep it**.
  - **(B) Content rewriting** — what the code does to the LLM's output *after* it writes it. Two kinds, and the difference is the whole point:
    - **Repair** (keep — defensive): margin fixes of *genuine* LLM output — the established cases above (cloud-SDK import, f-string braces, workflow `${{` sanitize, and the configmap **verbatim-embed** of the architect's OWN `monitoring_specs.json` / `setup_trino.sql`, which is anti-truncation, not a rewrite of judgment).
    - **Replace** (the LLM step is near-vestigial): the code regenerates the WHOLE artifact and keeps ~nothing from the LLM — the **Lakeview dashboard** (`_canonical_lakeview_dashboard` keeps only the `_audit` table name, which is already in config). Here the LLM call is mostly cost/latency/variance; the correct end state is to generate it from config and **drop the LLM** for that artifact.
  - **The rule:** the LLM owns **judgment under variability** (open inputs — an arbitrary source schema, NL business rules, error logs → real pandas/SQL/diagnosis). Deterministic code owns what is **mechanically determined** with one correct answer (naming, structure, wiring, boilerplate). **Anti-pattern:** an LLM step for a deterministic artifact with repair code underneath — if the generator already exists, remove the LLM. (Before refactoring a *validated* path toward this, weigh regression risk vs. gain — see the per-artifact variability table when one is added.)
- **Radical Honesty:** If an approach is wrong, say so. Don't execute bad instructions blindly.

---

## Credential Access — Absolute Rule

`cloud_get()` is the **only** permitted way to read DB credentials in generated **object-storage** pipeline scripts (AWS/GCP/Azure).

```python
from utils.cloud_config import cloud_get
host = cloud_get(cloud, "db_host", db_type="postgres")  # aws | gcp | azure
```

`os.getenv()` for `POSTGRES_DB_*` / `MYSQL_DB_*` is a **policy violation** — caught by `validate_generated_code`. The architect.md prompt explicitly forbids it. Resolution differs per cloud: **AWS** is 3-tier (SSM → `.bootstrap_outputs.json` → env); **GCP/Azure** read env vars directly (no SSM).

**Databricks is the exception — NOT `cloud_get()`:** a Databricks Spark script reads the source DB **password** via `dbutils.secrets.get(scope, "db_password")` (host/name/user arrive as **job parameters**). Using `cloud_get()` (or `os.getenv` for the DB) in a Databricks script is wrong — see `databricks_spark_standard.md`.

**Per-cloud runtime resolution:**
- **AWS:** SSM via IRSA. The pipeline pod's IAM role MUST carry `ssm:GetParameter*` on `arn:aws:ssm:*:*:parameter/multi-cloud-self-healing-agent/*` — in BOTH `bootstrap/aws/iam.tf` (existing IRSA role) and `knowledge_base/infrastructure/terraform_aws_s3.md` (infra-agent-generated policy). Missing it → `cloud_get()` returns `None` → `host name "None"` error. K8s db-credentials secret is created **empty**. SSM params use legacy names (`rds_host`…) resolved via `_SSM_KEY_CANDIDATES`.
- **GCP / Azure:** No SSM — `cloud_get()` reads env vars, so the K8s secret IS populated (`MYSQL_DB_*` / `CRM_DB_*`) from GitHub vars/secrets.
- **Databricks:** No `cloud_get()`. The **pipeline Terraform** reads the source DB connection from **SSM** (`/multi-cloud-self-healing-agent/aws/lakehouse_db_*`, host_cloud=aws) — the **password** flows into a Databricks **secret scope** (read by `dbutils.secrets.get`), host/name/user become job parameters. Distinct `lakehouse_db_*` keys avoid colliding with eu_sales `rds_*` in the shared `/aws/` namespace.

---

## Standalone Repository — Path Rules

This is **not a monorepo**. All paths are relative to the repo root:
- Docker build context: `.`
- Dockerfile: `Dockerfile`
- K8s applies: `k8s/job.yaml`
- GHA trigger: `on: push` scoped to **artifact paths only** (`Dockerfile`, `scripts/pipe_*.py`, `k8s/**`, `sql/**`, `dashboards/**`, `requirements.txt`). Never `paths: ['**']`, and never `scripts/**` — `scripts/` also holds agent-side utilities (seed_chaos, ingest_to_pinecone), and a lint touch to those must NOT redeploy the pipeline (bitten 2026-06-10). Standards/prompt/agent-code edits must NOT trigger a pipeline redeploy.
- Never use `projects/multi-cloud-self-healing-agent/` anywhere.

---

## Agent Routing — Key Invariants

**Supervisor RULE A (architect just ran):**
1. `agent_error` flag → medic (cleared to False)
2. `architect_status == "completed"` → infra

**Fix mode — architect:**
- Uses `patch_project_file` (surgical), never `write_project_file` (full rewrite).
- If patch + auto-validation succeed (`patch_clean_files` non-empty), `any_tool_error` is overridden to False — an unauthorized secondary tool call must not block success.

**`healing_context` is one-shot:**
- Set by medic's `request_fix`. Read by architect or infra. Cleared to `""` by whichever agent consumes it. Never leaks between agents.
- Multiple `request_fix` calls in one medic turn **accumulate** (not overwrite) the healing_context.

**`medic_fix_requested` lifecycle:**
- Scenario A (github_done=False): architect clears it → infra starts from scratch.
- Scenario B (github_done=True): architect keeps it → infra skips terraform, goes straight to push.

**ECR URL source:** Single source of truth is **SSM** (`/multi-cloud-self-healing-agent/<cloud>/ecr_repository_url`), written by bootstrap (`bootstrap/aws/ssm.tf`) since bootstrap creates the registry. The infra agent reads it back via `cloud_get_infra()` (SSM → `.bootstrap_outputs.json` fallback for local dev). Never parse it from the infra agent's terraform output (that terraform only makes S3 + IAM, not ECR) and never self-assemble from account_id. The resolved URL is injected into the prompt; the pipeline `job.yaml` carries the `RESOLVE_FROM_EXECUTE_TERRAFORM_OUTPUT` sentinel which the CI `sed` step rewrites to the real URL.

**CI polling (medic):** 5 retries with exponential backoff (30s → 60s → 120s → 240s → 300s). After attempt 5 the run is TERMINAL: medic surfaces the message, sets `fix_loop_escalated` + `mission_status="ci_unverified"`, supervisor routes FINISH deterministically (never the LLM fallback — that used to re-enter polling with a fresh counter and could sleep for hours toward the recursion limit).

**403 from `fetch_github_action_logs`:** Token permissions issue, not a code bug. Returns `PENDING: PERMISSIONS_ERROR` → medic tells user to fix GH_TOKEN scope, does NOT call `request_fix`.

**`mission_status` — the terminal outcome contract:** `"verified"` (medic verified e2e — the ONLY success) | `"escalated"` (fix loop abandoned / operational blocker) | `"ci_unverified"` (CI result never arrived) | `""` (unverified FINISH, e.g. LLM fallback — fails closed). Set at the medic/supervisor terminal points; consumed by the entry points: `main.py` throws `MissionFailedError` INTO the stream on an unverified FINISH (LangSmith root run → status error) and exits 1 (GitHub Action → red); Streamlit shows ❌ instead of the success banner. "The graph ran to completion" is NEVER success by itself.

**Medic must be evidence-grounded (anti-hallucination):** `medic.py` parses `state["messages"]` at the Python layer into a structured VALIDATION SUMMARY (FAILED files with verbatim error text + CLEAN files) injected into the prompt — the LLM never scans raw messages to "discover" errors. `request_fix` has a required `evidence_quote` param and the tool **rejects the call** if it contains no real error marker (`VALIDATION FAILED`, `Error:`, `FAILED`, `Exception`, `Traceback`, `exit code`, `rejected`, plus kubectl markers `is invalid` / `Invalid value` / `immutable`). The marker list must cover every real failure source the medic quotes — a genuine CI error whose text matches no marker (e.g. a kubectl `The Job … is invalid: … field is immutable`) is wrongly rejected, leaving `medic_fix_target` empty so the supervisor's target derivation falls back to its `"architect"` default and routes the infra fix to the wrong agent. Only FAILED files may be passed to `request_fix`; CLEAN files are off-limits.

---

## Generated Artifacts — Critical Rules

**Python pipeline scripts:**
- `storage_options=dict()` is mandatory in every `to_parquet()` call (use `dict()`, not `{}`, so the model can't double-brace it into `{{}}`).
- Trino partition registration uses the literal catalog `hive` (never a `catalog` variable — filling it with the literal leaves it unused → ruff F841).
- `create_engine` AND the extraction loop in the **same** `try` block.
- `destination_uri = os.getenv("DESTINATION_URI")` — never hardcode a URI string in the script.
- **Full three-cloud skeleton (never collapse):** a generated script keeps ALL THREE `if _CLOUD == "aws":` / `elif _CLOUD == "gcp":` / `elif _CLOUD == "azure":` branches (real bodies) in the cloud-SDK import, idempotency, and credentials blocks — the cloud-agnostic structure the validated AWS/Azure pipelines use; only the active cloud's branch runs at runtime. This is the proven-reliable form. Do NOT collapse to one branch — collapsing is where the model intermittently drops the cloud-SDK import (→ F821) or flattens the `if _CLOUD ==` guard (→ CLOUD-GUARD failure). And NEVER leave a branch empty/comment-only (a comment-only `elif` is a `SyntaxError` that `patch_project_file`'s safety-net rejects, dead-looping the self-heal). (Earlier we briefly required single-cloud collapse — `a87adf1` — and it introduced exactly that variance; reverted to the full skeleton.)
- **Azure idempotency:** derive the blob container from the abfss netloc via `parsed.netloc.split('@')[0]` — the netloc is `container@account.dfs.core.windows.net`, NOT the bare container (passing the whole netloc → HTTP 400 `InvalidResourceName`). `s3://`/`gs://` put the bucket directly in netloc, so the split is azure-only.
- Type casting mandatory: `float64` → `Int64` for quantity/count columns before every `to_parquet()`.
- Business rules (`quality_standards`) must be real pandas code — never `is_suspicious = False`.
- **Numeric comparison columns coerce, never `.astype(float)`:** a column compared numerically (`> 0`, `>= 0`, …) may hold dirty source values (`'not_a_number'`). Coerce with `pd.to_numeric(chunk[col], errors='coerce')` (dirty → NaN → dropped as a rejected row). `.astype(float)` raises on the first bad cell and crashes the whole run. `validate_generated_code` flags `.astype(float)`. (`.astype('Int64')` for the final integer cast is unrelated and still required.)
- `FLAG_AS_SUSPICIOUS` → `chunk['is_suspicious'] = ~condition`. Multiple rules: combine with `|`.
- `run_date` is a Hive partition key from the path — never add it as a DataFrame column.

**`requirements.txt`:** At the **repo root** — never in `scripts/`. Always include the filesystem driver: `s3fs` (AWS), `gcsfs` (GCP), `adlfs` (Azure).

**K8s mandatory object counts:**

| File | Objects |
|---|---|
| `00_namespaces.yaml` | 3 — analytics Namespace + monitoring Namespace + ServiceAccount |
| `trino_deployment.yaml` | 2 — Deployment + ClusterIP Service named `trino` |
| `grafana_deployment.yaml` | 2 — Deployment + LoadBalancer Service (cloud annotation) |
| `prometheus_deployment.yaml` | 4 — Prometheus Dep + Svc + Pushgateway Dep + Svc |
| `configmaps.yaml` | 5 ConfigMaps |

`hive-catalog-config` data key is **always** `hive.properties` — never `catalog.properties`.

**K8s secret names:** RFC 1123 — lowercase + hyphens only. `pipe-eu-sales-to-s3-db-credentials` ✓. Same name in GHA workflow and `job.yaml`.

**Dockerfile:** `ENV PYTHONPATH=/app` is mandatory — `python scripts/x.py` adds `/app/scripts` (not `/app`) to `sys.path`, so `from utils.cloud_config import cloud_get` fails at runtime even with `COPY utils/ utils/` present.

**Grafana `grafana-dash-config` ConfigMap** needs TWO keys: `dashboard-provider.yaml` (the file-provider config pointing at `/etc/grafana/provisioning/dashboards`) AND `monitoring_specs.json`. Without the provider YAML, Grafana starts but provisions zero dashboards.

**Grafana dashboard panels** must filter by the `$project_id` template variable (`project_id=~"$project_id"`), never a hardcoded `project_id="..."` — a literal never matches the runtime metric label (set from `PROJECT_ID`, default `unknown`) → every panel shows "No data". `validate_generated_code` enforces this on `monitoring_specs.json` (rejects a hardcoded `project_id` or a missing `$project_id` template var). The dashboard `uid` is derived from the pipeline name (stable across runs); with `disableDeletion: false` a uid change makes Grafana drop the old dashboard and provision the new one on restart.

**AWS account ID:** `aws_account_id` comes from `CLOUD_SETUP.aws_account_id` in context — used for the IRSA role ARN in `00_namespaces.yaml`. Never derive it from the ECR URL or write a `<...>` placeholder. (ECR URL itself: see "ECR URL source" above.)

**Databricks pipeline artifacts (NOT the K8s model above):** when `provider: databricks` the artifacts are the Spark script (`scripts/<pipeline>.py`), the Unity Catalog DDL (`sql/setup_unity_catalog.sql`), the **Lakeview dashboard JSON** (`dashboards/<pipeline>_lakeview.json`), and the 5 pipeline Terraform files — **no `requirements.txt`, no `Dockerfile`, no `k8s/`, no Grafana/Prometheus** (the cluster runtime provides pyspark+delta; the source JDBC driver is a Maven `library` on the job). The script does Spark JDBC read → Delta `saveAsTable` (Unity Catalog), and writes a `_audit` Delta table (one row per run) in place of the Prometheus gauges. **Observability = a Databricks Lakeview (AI/BI) dashboard** (the native equivalent of the other clouds' Grafana): the architect generates `dashboards/<pipeline>_lakeview.json` from the skeleton in `databricks_spark_standard.md` (datasets querying the `_audit` table → counters + line + bar widgets, `queryLines` not `query`, no `$`-template var), and the pipeline Terraform provisions it with `databricks_dashboard` (reads the JSON via `file_path`, on the bootstrap serverless SQL warehouse resolved by `data "databricks_sql_warehouse"`). The dashboard JSON is a `required_artifacts` entry (enforced by `_resolve_artifacts`) and is pushed under `dashboards/`. Full skeletons in `databricks_spark_standard.md` + `terraform_databricks.md`. Key script invariants: the **bare** `<table_name>` built into `f"{catalog}.{schema}.{table}"` (never a pre-qualified 5-part name); `?sslmode=require` + connect/socket timeouts on the JDBC URL (RDS forces SSL, pgjdbc's default `prefer` hangs); the `py4j` logger silenced to WARNING (root `INFO` floods one line per JVM call → driver bottleneck); `.cache()` on the JDBC read (the business-rule counts re-scan it).

---

## CI/CD — GitHub Actions

**Secrets vs Variables (the split the standard enforces):**
- **GitHub Secrets** (sensitive): `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `*_DB_PASSWORD`, `AZURE_CREDENTIALS`, `GH_PAT`.
- **GitHub Variables** (`${{ vars.* }}`, non-sensitive): `AWS_DEFAULT_REGION`, DB `HOST`/`PORT`/`USER`/`NAME`. Never write a literal region (`eu-central-1`) — always `${{ vars.AWS_DEFAULT_REGION }}`.

**Azure `us_crm` (validated e2e 2026-06-04 — alongside AWS `eu_sales`):** Before running `pipeline: us_crm`, the azure bootstrap (`bootstrap/azure/`) MUST have applied (AKS + ACR + storage + Postgres + managed identity). Required repo **Secrets**: `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`, `ARM_SUBSCRIPTION_ID`, `ARM_TENANT_ID` (infra-agent Terraform), `AZURE_CREDENTIALS` (generated deploy workflow's `azure/login`), `CRM_DB_PASSWORD`, `AZURE_DB_PASSWORD`. Source is **Azure Postgres** (`postgresql+psycopg2`, port 5432) — not MSSQL. Required **Variables**: `CRM_DB_HOST`, `CRM_DB_PORT`, `CRM_DB_USER`, `CRM_DB_NAME`. Azure resolves DB creds via `CRM_DB_*` (no SSM) — see `_ENV_FALLBACKS` in `utils/cloud_config.py`.

**`GH_PAT` (classic PAT, scopes `repo` + `workflow`):** Used by both `actions/checkout` (in `run_agent.yml`) and `push_to_github`, NOT the built-in `GITHUB_TOKEN` — the built-in lacks `workflow` scope (403 on `.github/workflows/` pushes) and its pushes don't re-trigger workflows. Set as `GH_PAT` in repo Secrets AND `GITHUB_TOKEN=<same PAT>` in local `.env`; `push_to_github` infers the repo slug from the remote URL when `GITHUB_REPOSITORY` is absent.

**The generated pipeline workflow does NOT use the `gh` CLI** — no `GH_TOKEN` env block in the job. Git auth in the deploy workflow is handled by the cloud (AWS/GCP/Azure) credentials.

**GCP image tag — `:latest`, no tag-rewrite sed:** the GCP `job.yaml` references `<ecr_repository_url>:latest` (the build pushes both `:${{ github.sha }}` and `:latest`). There is **NO "Set Image Tag" `sed` step** for GCP: the model intermittently appends a `-<timestamp>` to the AR image name, which makes a tag-rewrite `sed` no-op, leaving the literal `${{ github.sha }}` to reach the cluster as `InvalidImageName`. `:latest` needs no rewrite. **Never put `${{ … }}` in any k8s manifest** (Kubernetes never evaluates it) — `validate_generated_code` flags it for GCP. (AWS/Azure keep their anchored `…:.*` → `…:${{ github.sha }}` sed — they don't add a timestamp.)

**Databricks `sales_lakehouse` (validated e2e 2026-06-08):** Before running `pipeline: sales_lakehouse`, the databricks bootstrap (`bootstrap/databricks/`) MUST have applied (workspace + Unity Catalog + **1-worker jobs cluster** + serverless SQL warehouse + its own source RDS + SSM). There is **no Docker build, no K8s** — the deploy workflow uploads the script to DBFS, runs the pipeline Terraform (secret scope + `databricks_job`), then `jobs run-now` to verify. The pipeline + deploy authenticate **AS the service principal** (`oauth-m2m` via `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`), so the SP self-binds into the job's `run_as` (a user PAT can't — needs servicePrincipal.user role) and the job inherits the SP's UC access; `auth_type` is pinned to `oauth-m2m` so the databricks provider ignores the agent env's `ARM_*`/`GOOGLE_CREDENTIALS` ("more than one authorization method configured"). The deploy ALSO needs AWS creds (terraform init's S3 backend + reading the `lakehouse_db_*` SSM params). DB creds come from **SSM, not a GitHub secret**. Required repo **Secrets**: `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` (+ `TF_VAR_databricks_client_id`), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.

---

## Prompt vs. Standard — Separation of Concerns

**Agent prompts (`agents/prompts/*.md`) answer "what to do and when":**
- Role definition, workflow steps in order, tool call sequences
- Routing decisions and absolute one-line prohibitions
- Structural invariants that must be correct 100% of the time (K8s object counts, mandatory file names) — these stay in the prompt for directness, even though the standard also defines them

**Standards (`knowledge_base/*.md`) answer "how exactly and why":**
- Full code skeletons and ❌/✅ examples
- Detailed mappings, data type tables, edge cases, WHY explanations
- Single source of truth for implementation detail

**Rules for adding a new requirement:**
- Needs a code example → standard only, prompt gets a one-line reference
- Structural invariant (must be right 100% of the time) → brief rule in prompt + full detail in standard
- If it lives in both places it will eventually diverge — pick one owner for the detail

**Rules for removing from a prompt:** Verify the rule exists in the standard first, then remove.

**Rules for modifying an existing rule:** Update the standard first (the owner), then update any prompt reference if the wording changed.

## Knowledge Base & Pinecone

Standards live in `knowledge_base/`. Pinecone loads the full content of each standard (no chunking) — every agent has access to the complete text.

**After editing any `knowledge_base/*.md` standard, the next `run_agent.yml` MUST set `sync_knowledge_base: sync`.** Pinecone serves the *last synced* version — without a re-sync the agents read the OLD standard and your edits are silently ignored. This applies to ALL standards for ALL agents — the infra agent's former disk-read override was removed in `742e340`; everything now goes through Pinecone.

The validator (`validate_generated_code`) is a **safety net** for architectural issues the LLM can't know from general knowledge (custom modules, cloud-specific quirks). It is not a substitute for correct prompts and standards. Do not add fragile regex checks for general Python best practices.

---

## NL Authoring & Streamlit Demo Surface (NOT the agent pipeline)

`utils/nlp_parser.py`, `streamlit_app.py`, `utils/cost_estimator.py`, and `.streamlit/config.toml` are a **demo/portfolio surface** for *creating* a pipeline from natural language. They are **separate from the agent generation path** — changes here must NOT touch `agents/`, `knowledge_base/`, the working pipeline configs, or the YAML run path (`main.py <pipeline>`), which is how the 4 validated clouds run. Breaking that is the one thing to avoid.

- **NL flow:** free text → `_extract_intent` (gpt-4o-mini) → constrained `PipelineIntent` (enums) → Streamlit wizard / CLI clarification loop → business rules (typed / extracted / file via `parse_rules_from_content` / AI suggestions) → `_build_from_answers` builds the bundle and `_save_generated_configs` writes the **4 per-pipeline** configs (pipeline / objective / database / business_rules). The infra config (`configs/infra/<cloud>.yaml`) is shared-static, NOT generated. The user supplies only the basics (description + a few fields + rules); the system generates the rest.
- **NL authoring = the 3 object-storage clouds (Option A).** Databricks is a fixed demo + a peer only in the cost comparison (its NL source is fixed, so a free-text builder adds nothing).
- **In-memory vs disk:** the live run uses the IN-MEMORY bundle (`raw_configs` in the graph state), NOT the files — the disk write is only for re-run via `main.py <name>`. A slug colliding with an existing pipeline OVERWRITES its configs (use a distinct name for NL demos).
- **Relevance gate:** extraction returns `is_pipeline_request`; off-topic input is rejected (`build_pipeline_bundle_from_nl` raises; `check_pipeline_request()` for UIs). Wired into BOTH Streamlit NL doors (wizard step 0 + advanced "Analyze & Plan").
- **Real NL-deploy** needs the target cloud's bootstrap LIVE + `.bootstrap_outputs.json` exported; state-backend defaults are `REQUIRES_BOOTSTRAP_OUTPUTS` sentinels (fail-loud) when outputs are absent. The Streamlit "Deploy" runs the agent graph (PIPELINE-level only) — it does NOT bootstrap (cluster/registry/state/DB are the separate one-time `bootstrap/`).
- **Cost:** `utils/cost_estimator.py` — deterministic, list prices, the fixed bootstrap footprint per cloud (all 4). Streamlit wizard step 3 = config summary + execution plan + cost comparison BEFORE the explicit "Confirm & Deploy" (preview-then-deploy).
- **`.streamlit/config.toml`** forces the dark theme (the UI is dark-designed; light mode breaks) + `toolbarMode="viewer"` (hides Streamlit's own Deploy button). Config changes need a Streamlit RESTART, not a rerun.

---

## Git Workflow

Commit and push after every coherent set of changes — no per-change confirmation gate. One logical change per commit.

Format: `<type>(<scope>): <description>`
Types: `feat | fix | infra | docs | refactor | test | chore`
Scopes: `architect | infra | medic | supervisor | bootstrap | configs | knowledge-base | ci | streamlit`

Never commit `.env` files or credential JSON. pre-commit hooks (ruff, gitleaks, terraform fmt) and CI (`tests.yml`, `security.yml`) gate every push.

---

## Before Any Change — Checklist

- Is this cloud-agnostic? Works equally on AWS, GCP, Azure? (Databricks is a separate execution model — does the change belong to the object-storage clouds, to Databricks, or to both?)
- Am I fixing the standard/prompt (root cause) or patching the output (symptom)?
- Am I using `cloud_get()` for credentials? (Databricks: `dbutils.secrets.get` instead — never `cloud_get()`.)
- Does the fix need a code example? → standard. Is it a one-line prohibition? → prompt. See "Prompt vs. Standard" section.

**Never edit generated artifacts to get a quick result.** `scripts/pipe_*.py`, `k8s/`, `sql/`, `dashboards/`, `terraform/`, `.github/workflows/<pipeline>_pipeline.yml`, `Dockerfile`, `requirements.txt` are OUTPUTS — fix the standard/prompt and let the next agent run regenerate them. (`scripts/seed_chaos.py`, `scripts/ingest_to_pinecone.py`, `scripts/export_bootstrap_outputs.py` are agent-side utilities — normal code.) The only exception is repo-infrastructure the agent does not generate (e.g. `run_agent.yml`, `bootstrap/`).

---

## Verifying a Run / Operations

Full per-cloud verification steps, failure signatures, and teardown procedures live in **`docs/RUNBOOK.md`** — read it before debugging a "run succeeded but X looks wrong" report. The four invariants that bite during *code* changes:

- **Grafana "No data" ≠ pipeline failure.** Order of checks: metrics in Prometheus? → stale `grafana-dash-config` ConfigMap on the cluster (lags the committed artifact)? → idempotency skip (existing `run_date=` partition returns *before* metrics; Pushgateway is in-memory).
- **Grafana login:** `admin` / the `grafana-admin` K8s secret (pre-hardening deployments: admin/admin).
- **Databricks `[INSUFFICIENT_PERMISSIONS] … USE CATALOG` on all widgets** = the bootstrap `account users` catalog grant is missing (viewer-credential dashboards) — NOT a data problem.
- **Databricks teardown is two-phase** (`destroy.yml`): runtime-created managed tables require `force_destroy`/`force_update` flags applied into state before `terraform destroy` — a plain destroy always fails.
