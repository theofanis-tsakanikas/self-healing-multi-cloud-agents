# Professionalization pass — status & plan

Tracks the hardening work on `feat/professionalization`. Honest about what shipped, what is
deliberately deferred (and why), and what the owner must run. The guiding rule of this pass: **never
ship a change to the runtime deployment path that cannot be validated offline** — the bootstrap is
down, so anything that can only be proven by a live cloud run is implemented behind tests and flagged
here rather than merged blind.

## ✅ Shipped (offline-validated: `make lint && make test` green)

| Area | Change | Proof |
|---|---|---|
| Correctness landmine | Reverted the injected demo chaos — `databricks_secret` key back to `db_password` | `test suite`, `DEMO_MISTAKES.md` marked REVERTED |
| Silent-drop hazard | Pinecone ingest fails LOUD on token overflow; CI budget test on every standard | `tests/test_embedding_budget.py` |
| Config drift | Unified Pinecone default index name (`unified-intelligence-fabric`) | — |
| Doc honesty | Cost table realigned + `python -m utils.cost_estimator` as source of truth; de-numbered test counts | `make cost` |
| Security gate | Policy-as-code over generated infra (Python source-of-truth + Rego 2nd engine) + gate-proof + SBOM | `tests/test_security_gate.py`, `make gate-proof` |
| Fragility | Load-bearing string contracts centralized + pinned | `tests/test_contracts.py` |
| Graph wiring | Full-graph routing integration test | `tests/test_graph_integration.py` |
| Ops | Credential-rotation & PAT→GitHub App runbook | `docs/CREDENTIAL_ROTATION.md` |

## ⏳ Owner must run (touches live systems — not automated)

1. **Re-sync Pinecone** so the reverted `db_password` key reaches the live vector store:
   `make ingest` (or `sync_knowledge_base: sync` on the next `run_agent.yml`). Until then the LIVE KB
   still serves the bad key.
2. **Credential rotation** — follow `docs/CREDENTIAL_ROTATION.md` (leaked PAT, PAT→GitHub App, `.env`).
3. **Branch protection** — enable required review + required checks (`tests`, `security`) on `main`;
   the repo is single-developer today with direct-to-main pushes.

## ⛔ Deferred — needs a live validation run (bootstrap is down)

Each of these changes the runtime output or deployment behavior in a way that golden/unit tests
cannot fully prove. Implementing them blind risks breaking the four validated deployments, which
violates the project's own "no shortcuts / radical honesty" principles. They are ready to do the
moment a bootstrap is live and one e2e run per touched cloud can confirm them.

| Item | Why it needs a live run |
|---|---|
| **Split `python_standards.md`** (at 8190/8191 tokens) | Splitting → two vectors; retrieval is top-k with a score floor, so a single query may fetch only one half. Needs a run to confirm the architect still generates a correct script. The loud-fail guard + budget test already neutralize the *silent-drop* hazard in the meantime. |
| **Enforce pod `securityContext` (`runAsNonRoot`)** | SECURITY.md #3: several upstream images (grafana/trino/prometheus) declare non-numeric users; a blanket `runAsNonRoot: true` fails kubelet and breaks the pods. Correct fix pins per-image numeric UIDs — must be confirmed the pods actually start. Surfaced as ADVISORY by the security gate today. |
| **Typed `PipelineSpec` → deterministic Trino DDL render** | Moves DDL generation from LLM-text-then-repair to code-owned render. Changes what the architect emits; needs a run to confirm generation + a golden refresh. Highest-leverage structural item. |
| **Constrained / structured decoding** for the remaining LLM outputs | Changes the LLM call shape; only a run proves the model still produces valid scripts. |
| **Terraform modules refactor** | State-address migration must run against live state or it corrupts it. |
| **Model-matrix evals** (`eval-live` across models) | Needs LLM API keys; proves the guards are model-agnostic. |

## 📋 Remaining offline-safe items (in progress on this branch)

- Durable LangGraph checkpointer (opt-in, so default behavior is unchanged).
- Structured JSON logging + correlation IDs (additive).
- Swappable RAG backend (the offline `local_kb` already exists; make it a first-class toggle).
- Raise deterministic-core coverage.
