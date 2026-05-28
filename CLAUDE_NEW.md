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
2. `arch_status == "completed"` → infra
3. keyword scan on last 5 messages → medic if errors found
4. else → architect again

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
- Business rules (`quality_standards`) must be real pandas code — never `is_suspicious = False`.
- `FLAG_AS_SUSPICIOUS` → `chunk['is_suspicious'] = ~condition`. Multiple rules: combine with `|`.
- `run_date` is a Hive partition key from the path — never add it as a DataFrame column.

**`requirements.txt`:** At the **repo root** — never in `scripts/`. Always include the filesystem driver: `s3fs` (AWS), `gcsfs` (GCP), `adlfs` (Azure).

**K8s mandatory object counts:**

| File | Objects |
|---|---|
| `trino_deployment.yaml` | 2 — Deployment + ClusterIP Service named `trino` |
| `grafana_deployment.yaml` | 2 — Deployment + LoadBalancer Service (cloud annotation) |
| `prometheus_deployment.yaml` | 4 — Prometheus Dep + Svc + Pushgateway Dep + Svc |
| `configmaps.yaml` | 5 ConfigMaps |

`hive-catalog-config` data key is **always** `hive.properties` — never `catalog.properties`.

**K8s secret names:** RFC 1123 — lowercase + hyphens only. `pipe-eu-sales-to-s3-db-credentials` ✓. Same name in GHA workflow and `job.yaml`.

---

## Knowledge Base & Pinecone

Standards live in `knowledge_base/`. After **any** change to a `.md` file there, run:
```bash
source .venv/bin/activate && python scripts/ingest_to_pinecone.py
```

The validator (`validate_generated_code`) is a **safety net** for architectural issues the LLM can't know from general knowledge (custom modules, cloud-specific quirks). It is not a substitute for correct prompts and standards. Do not add fragile regex checks for general Python best practices.

Structural rules that must be followed 100% every time (K8s object counts, key names) belong in the **agent prompt** (infra.md), not only in Pinecone — vector retrieval is unreliable for exact structural requirements.

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
- If I changed a knowledge base file, did I re-ingest to Pinecone?
- Does the fix require a validator addition or is it better handled at the prompt layer?
