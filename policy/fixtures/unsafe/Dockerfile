# UNSAFE FIXTURE — deliberately trips DOCKERFILE_ROOT_USER + DOCKERFILE_COPIES_ENV.
# Used by tests/test_security_gate.py to prove the gate REFUSES bad generated artifacts. Not deployed.
FROM python:3.11-slim
WORKDIR /app
COPY .env .env
COPY scripts/ scripts/
CMD ["python", "scripts/pipe.py"]
