# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); commit history uses
conventional commits (`type(scope): description`).

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
