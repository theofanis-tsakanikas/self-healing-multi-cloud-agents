# STANDARD: DOCKERFILE FOR DATA PIPELINE IMAGES

## MANDATORY STRUCTURE

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
COPY sql/ sql/
COPY dashboards/ dashboards/

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

CMD ["python", "scripts/<pipeline_script_name>.py"]
```

## CRITICAL RULES

- **Base image:** Always `python:3.11-slim` — never `python:3.11` (full image is 3x larger).
- **Script path in CMD:** The pipeline script lives in `scripts/`, not the root. CMD must be `["python", "scripts/<name>.py"]` — never `["python", "<name>.py"]`.
- **Selective COPY:** Never use `COPY . .`. Copy only the directories that the script needs: `scripts/`, `sql/`, `dashboards/`. This avoids leaking terraform files, .env, k8s manifests, and state files into the image.
- **Non-root user:** Always create and switch to a non-root user (`appuser`) before CMD. Kubernetes security policies reject root containers.
- **No comments:** Do not add inline comments to the Dockerfile — they increase layer size and add noise.
- **No .env files:** Never COPY .env or any file containing credentials. All secrets are injected at runtime via Kubernetes `envFrom: secretRef`.
