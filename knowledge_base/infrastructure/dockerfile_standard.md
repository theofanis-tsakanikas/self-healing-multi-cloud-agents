# STANDARD: DOCKERFILE FOR DATA PIPELINE IMAGES

## MANDATORY STRUCTURE

```dockerfile
FROM python:3.11-slim

WORKDIR /app

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

- **Base image:** Always `python:3.11-slim` — never `python:3.11` (full image is 3x larger).
- **Script path in CMD:** The pipeline script lives in `scripts/`, not the root. CMD must be `["python", "scripts/<name>.py"]` — never `["python", "<name>.py"]`.
- **Selective COPY — MANDATORY list:** Never use `COPY . .`. The following directories MUST ALL be present — omitting any one causes a runtime import error:
    1. `COPY scripts/ scripts/` — the pipeline entry point
    2. `COPY utils/ utils/` — **REQUIRED**: contains `cloud_config.py` which is imported by every pipeline script as `from utils.cloud_config import cloud_get`. Omitting this causes `ModuleNotFoundError: No module named 'utils'` at container startup.
    3. `COPY sql/ sql/` — Trino DDL scripts
    4. `COPY dashboards/ dashboards/` — Grafana dashboard JSON
- **Non-root user:** Always create and switch to a non-root user (`appuser`) before CMD. Kubernetes security policies reject root containers.
- **No comments:** Do not add inline comments to the Dockerfile — they increase layer size and add noise.
- **No .env files:** Never COPY .env or any file containing credentials. All secrets are injected at runtime via Kubernetes `envFrom: secretRef`.
