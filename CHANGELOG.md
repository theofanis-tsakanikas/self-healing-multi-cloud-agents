# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); commit history uses
conventional commits (`type(scope): description`).

## [Unreleased]

### Added
- **`docs/adr/` — eight decision records**, extracted from reasoning that already existed in
  `CLAUDE.md`, the prompts and the standards: standards-first generation, the LLM/deterministic
  boundary, the anti-hallucination evidence gate, bounded autonomy and the terminal contract,
  deterministic CI polling, no default cloud, Databricks as a distinct execution model, and the
  choice of a small model with the reliability in the harness. Each records what was **rejected**.
- **README sections `Decisions`, `What this does not do`, `Cost` and `Security`.** The limits already
  existed — in `SECURITY.md`, where a reader looking for them in the README would not find them; they
  are now summarised there with a pointer to the full version. `Cost` is new: three managed Kubernetes
  clusters and four managed databases bill by the hour, which is why `bootstrap` is per-cloud.
- **`The problem` as a heading.** The opening paragraphs already did the work but had no heading, so
  the Contents could not link to them.

### Changed
- **`Testing` now states a number** — **380 tests**, hermetic — and what they do *not* cover: the
  LLM's output quality (which is what the eval harness is for) and anything requiring a cloud.
- **Dependabot version updates switched off** (`open-pull-requests-limit: 0`), matching the rest of
  the portfolio. The existing reasoning is kept verbatim — the `lockfile-only` strategy and its
  pyarrow-cap explanation, and the major-version `ignore` for workflows a PR's CI never exercises —
  because turning updates back on should be one line, not a re-derivation.

### Note
No agent code, standard, config, bootstrap or workflow was modified. This release is documentation
and repository configuration only.


### Added
- **Offline replay + eval harness for the self-healing Medic** ([`evals/`](evals/), [docs/EVAL_HARNESS.md](docs/EVAL_HARNESS.md)) — makes the agent's core loop verifiable and its LLM judgment measurable, with no cloud, spend, or credentials:
  - `evals/corpus/corpus.json` — a golden corpus of the documented failure classes (script-logic → architect; missing library/secret/resource → infra; plus clean/green/speculation negatives), with realistic trigger logs matching the Medic's routing signatures and links to the real self-heal commits.
  - **Replay mode** (`make eval-replay`) — `evals/harness/` scores the *real* deterministic routing (`_ci_error_owner` + the failing-file fallback) and the anti-hallucination evidence gate (`request_fix`) against the corpus, writing `evals/report/{metrics.json,REPORT.md}`. The regression net the whack-a-mole guards never had — gated in CI (`make eval-check`) and asserted by `tests/test_evals.py`.
  - **Eval mode** (`make eval-live`) — `evals/harness/eval_live.py` scores the current model's diagnosis quality (correct target + gate-valid verbatim evidence) via the real `request_fix` tool; model-agnostic through the `get_llm` seam (OpenAI / Anthropic / Vertex).
  - **`heal` CLI** (`make heal LOG=…`, `evals/heal.py`) — routes + validates ANY failing CI log through the shipping Medic logic, decoupling the healing judgment from the pipelines this agent generated.
  - `evals/harness/local_kb.py` — a credential-free local knowledge-base retriever (stand-in for the Pinecone `query_vector_store` seam).

## [1.0.0] — 2026-06-10

First tagged release: **all four platforms validated end-to-end on live
infrastructure**, followed by a documentation, security, and engineering-hygiene
hardening pass.

### Highlights
- Multi-agent LangGraph system (Supervisor → Architect → Infra → Medic) that
  designs, deploys, and self-heals data pipelines from a YAML config or a
  plain-English description.
- Validated end-to-end: AWS `eu_sales` & Azure `us_crm` (2026-06-04),
  GCP `global_marketing` (2026-06-06), Databricks `sales_lakehouse` (2026-06-08).
- Two execution models behind one `provider:` switch: object-storage
  (pandas → Parquet → Trino → Grafana on EKS/AKS/GKE) and lakehouse
  (Spark → Delta → Unity Catalog → Lakeview).
- Evidence-grounded Medic: diagnosis from a Python-built validation summary;
  `request_fix` rejects any claim that doesn't quote a real error marker.
- NL authoring demo (Streamlit): free text → typed pipeline config, business-rule
  extraction, and a 4-platform cost comparison before deploy.

### Security
- Grafana no longer ships default `admin/admin` on its public LoadBalancer —
  the deploy workflow provisions a `grafana-admin` Secret (repo secret or
  fail-secure random) read via `GF_SECURITY_ADMIN_PASSWORD`.
- Least-privilege `permissions: contents: read` on all workflows (repo +
  generated, via the CI/CD standard); `databricks/setup-cli` pinned to a
  release tag instead of `@main`.
- New `security.yml` CI: gitleaks (full-history, blocking), trivy dependency
  CVEs (blocking), trivy IaC misconfig (report-only — the public-endpoint
  findings are the documented demo posture).
- `SECURITY.md`: what is hardened vs. the six deliberate demo trade-offs, each
  with its production posture.

### Fixed
- Streamlit NL-wizard deploy raised `NameError`: `_start_run` was defined after
  its module-level call sites (Streamlit executes top-to-bottom); moved before
  all three uses.
- Repo-wide lint to zero (41 ruff findings: unused imports/variables, ambiguous
  names, one-line compound statements); CI previously linted only `tests/`.

### Added
- Flagship `README.md` (validated-e2e matrix, agent-graph diagram,
  source-vs-generated repo map) and as-built `docs/ARCHITECTURE.md`.
- Committed `.terraform.lock.hcl` for all four bootstrap stacks
  (linux_amd64 + darwin_arm64) and a `terraform fmt` CI gate for `bootstrap/`.
- `.pre-commit-config.yaml` (ruff, gitleaks, terraform fmt, hygiene hooks —
  generated artifacts excluded) and Dependabot (uv + GitHub Actions, weekly).
- Makefile: `bootstrap-databricks` / `demo-databricks`; `run-all` covers all
  four pipelines; `sales_lakehouse` selectable as a chaos target.

### Changed
- Generated pipeline image standard aligned on `python:3.12-slim` (was 3.11;
  project and CI run 3.12).
- `black`/`isort` dropped (unused; ruff owns lint/format), coverage floor
  raised 22% → 28% (real: ~32%).
- Stale `MASTER_PLAN.md` retired; `PRODUCT_VISION.md` → `docs/VISION.md` with
  post-Databricks status corrections.
