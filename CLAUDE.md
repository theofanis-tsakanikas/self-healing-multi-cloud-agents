# CLAUDE.md — Multi-Cloud Self-Healing Data Engineer Agent

## Project Identity

Production-grade AI orchestration system that autonomously designs, deploys, and self-heals data pipelines across AWS, Azure, and GCP using a LangGraph multi-agent architecture: **Supervisor → Architect → Infra → Medic**.

---

## Non-Negotiable Principles

- **Cloud Agnostic:** AWS, GCP, Azure are equals. No cloud is the default. Cloud is always read from `cloud_provider` in config — never assumed.
- **No Shortcuts:** Production solution always. No TODOs, no placeholders, no "simplified for now".
- **Standards First:** When the LLM generates wrong output, fix the **standard or prompt** — not the generated file. Hardcoded one-off fixes are never the answer.
- **Radical Honesty:** If an approach is wrong, say so. Don't execute bad instructions blindly.

---

## Credential Access — Absolute Rule

`cloud_get()` is the **only** permitted way to read DB credentials in generated pipeline scripts.

```python
from utils.cloud_config import cloud_get
host = cloud_get(cloud, "db_host", db_type="postgres")  # aws | gcp | azure
```

`os.getenv()` for `POSTGRES_DB_*` / `MYSQL_DB_*` is a **policy violation** — caught by `validate_generated_code`. The architect.md prompt explicitly forbids it. Three-tier resolution: SSM → `.bootstrap_outputs.json` → env fallback.

---

## Standalone Repository — Path Rules

This is **not a monorepo**. All paths are relative to the repo root:
- Docker build context: `.`
- Dockerfile: `Dockerfile`
- K8s applies: `k8s/job.yaml`
- GHA trigger: `on: push` with no `paths:` filter (or `paths: ['**']`)
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

**ECR URL source:** Always from `.bootstrap_outputs.json` — never from infra agent's terraform output. Bootstrap creates the registry; infra just uses it.

**CI polling (medic):** 5 retries with exponential backoff (30s → 60s → 120s → 240s → 300s). After attempt 5: surface message to user, route to supervisor, reset counter. Never loops to recursion limit.

**403 from `fetch_github_action_logs`:** Token permissions issue, not a code bug. Returns `PENDING: PERMISSIONS_ERROR` → medic tells user to fix GH_TOKEN scope, does NOT call `request_fix`.

---

## Generated Artifacts — Critical Rules

**Python pipeline scripts:**
- `storage_options={}` is mandatory in every `to_parquet()` call.
- `create_engine` AND the extraction loop in the **same** `try` block.
- `destination_uri = os.getenv("DESTINATION_URI")` — never hardcode a URI string in the script.
- Type casting mandatory: `float64` → `Int64` for quantity/count columns before every `to_parquet()`.
- Business rules (`quality_standards`) must be real pandas code — never `is_suspicious = False`.
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

The validator (`validate_generated_code`) is a **safety net** for architectural issues the LLM can't know from general knowledge (custom modules, cloud-specific quirks). It is not a substitute for correct prompts and standards. Do not add fragile regex checks for general Python best practices.

---

## Git Workflow

After every set of changes:
1. `git diff` → show full output.
2. Ask: **"Commit and push? [yes / no]"**
3. If yes: conventional commit message + push.

Format: `<type>(<scope>): <description>`
Types: `feat | fix | infra | docs | refactor | test | chore`
Scopes: `architect | infra | medic | supervisor | bootstrap | configs | knowledge-base | ci`

Never commit `.env` files or credential JSON.

---

## Before Any Change — Checklist

- Is this cloud-agnostic? Works equally on AWS, GCP, Azure?
- Am I fixing the standard/prompt (root cause) or patching the output (symptom)?
- Am I using `cloud_get()` for credentials?
- Does the fix need a code example? → standard. Is it a one-line prohibition? → prompt. See "Prompt vs. Standard" section.
