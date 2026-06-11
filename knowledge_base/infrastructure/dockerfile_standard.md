---
id: dockerfile-standard
applies_to: aws, azure, gcp (object-storage)
primary_consumer: infra-agent   # retrieved via Pinecone (query_vector_store); medic may also retrieve it
enforced_by: agents/codegen.py (deterministic render) + validate_generated_code (safety net)
last_reviewed: 2026-06-11
---

> **GENERATION: CODE-OWNED.** This artifact is rendered deterministically by
> `agents/codegen.py:render_dockerfile` (golden-tested in tests/test_codegen.py).
> This document is the SPEC for that generator and the Medic's diagnostic
> reference — changing a rule here requires changing the render in the same commit.

# STANDARD: DOCKERFILE FOR DATA PIPELINE IMAGES

## MANDATORY STRUCTURE

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
COPY utils/ utils/
COPY sql/ sql/
COPY dashboards/ dashboards/

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

CMD ["python", "scripts/<pipeline_script_name>.py"]
```

## CRITICAL RULES

- **Base image:** Always `python:3.12-slim` — never the full `python:3.12` (3x larger). 3.12 matches the project's `requires-python` and the CI interpreter — one Python version everywhere.
- **Script path in CMD:** The pipeline script lives in `scripts/`, not the root. CMD must be `["python", "scripts/<name>.py"]` — never `["python", "<name>.py"]`.
- **Selective COPY — MANDATORY list:** Never use `COPY . .`. The following directories MUST ALL be present — omitting any one causes a runtime import error:
    1. `COPY scripts/ scripts/` — the pipeline entry point
    2. `COPY utils/ utils/` — **REQUIRED**: contains `cloud_config.py` which is imported by every pipeline script as `from utils.cloud_config import cloud_get`. Omitting this causes `ModuleNotFoundError: No module named 'utils'` at container startup.
- **`ENV PYTHONPATH=/app` is MANDATORY** — without it, running `python scripts/script.py` adds `/app/scripts` to `sys.path` instead of `/app`, so `from utils.cloud_config import cloud_get` fails at runtime even though `COPY utils/ utils/` is present. This env var ensures Python always resolves imports relative to the WORKDIR.
    3. `COPY sql/ sql/` — Trino DDL scripts
    4. `COPY dashboards/ dashboards/` — Grafana dashboard JSON
- **Non-root user:** Always create and switch to a non-root user (`appuser`) before CMD. Kubernetes security policies reject root containers.
- **No comments:** Do not add inline comments to the Dockerfile — they increase layer size and add noise.
- **No .env files:** Never COPY .env or any file containing credentials. All secrets are injected at runtime via Kubernetes `envFrom: secretRef`.
