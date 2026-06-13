# Dockerfile for the data pipeline
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

CMD ["python", "scripts/pipe_etl_from_postgress_to_s3_to_s3.py"]
