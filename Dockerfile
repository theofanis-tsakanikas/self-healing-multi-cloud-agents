FROM python:3.11-slim

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

CMD ["python", "scripts/pipe_eu_sales_to_s3.py"]