## What

<!-- One paragraph: what changes and why. -->

## Checklist

- [ ] **No generated artifacts edited by hand** — `scripts/pipe_*.py`, `k8s/`, `sql/`, `dashboards/`, `terraform/`, `Dockerfile`, `requirements.txt`, `.github/workflows/pipe_*.yml` are agent OUTPUTS; fixes go to `knowledge_base/` standards or `agents/prompts/`.
- [ ] If a `knowledge_base/*.md` standard changed: the next `run_agent.yml` run must set `sync_knowledge_base: sync` (Pinecone serves the *last synced* version).
- [ ] Cloud-agnostic: works equally on AWS / Azure / GCP — and Databricks considered separately where the rule applies to it.
- [ ] `make lint` and `make test` pass locally (the full hermetic test suite, no credentials needed).
- [ ] Conventional commit message: `type(scope): description`.
