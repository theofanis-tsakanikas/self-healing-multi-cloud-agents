import os
import time
import datetime
import logging
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import create_engine
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from utils.cloud_config import cloud_get

_CLOUD = os.getenv("CLOUD_PROVIDER", "azure")

logging.basicConfig(level=logging.INFO)

def run():
    logging.info("Pipeline starting: US CRM Insights Job")

    run_date = datetime.date.today().isoformat()
    destination_uri = os.getenv("DESTINATION_URI")
    partition_uri = f"{destination_uri}run_date={run_date}/"

    # IDEMPOTENCY CHECK
    parsed = urlparse(partition_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip('/')

    # Check if the destination already has data for today
    # (Assuming Azure SDK is used to check the existence of blobs)
    # Implement the logic to check if the partition already exists

    # CREDENTIALS via cloud_get()
    host = cloud_get("azure", "db_host", db_type="postgres")
    user = cloud_get("azure", "db_user", db_type="postgres")
    pw = cloud_get("azure", "db_password", db_type="postgres")
    db = cloud_get("azure", "db_name", db_type="postgres")
    connection_string = f"postgresql+psycopg2://{user}:{pw}@{host}:5432/{db}"

    start_time = time.time()
    total_rows = 0
    rejected_by_reason = {}

    query = "SELECT * FROM raw_us_crm"

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):
            # Anonymization
            chunk['full_name'] = chunk['full_name'].apply(lambda x: hashlib.sha256(x.encode()).hexdigest())
            chunk['email_address'] = chunk['email_address'].str.replace(r'(^[a-zA-Z0-9._%+-]+)([a-zA-Z0-9._%+-]+@)', r'\1***@', regex=True)
            chunk['phone_number'] = chunk['phone_number'].str.replace(r'([0-9]{3})[0-9]{4,}()', r'\1***', regex=True)

            # Write to Parquet
            chunk.to_parquet(
                f"{partition_uri}part_{i}.parquet",
                engine="pyarrow",
                compression="snappy",
                index=False,
                storage_options={}
            )
            logging.info(f"Chunk {i}: {len(chunk)} rows processed")
            total_rows += len(chunk)

    except Exception as e:
        logging.error(f"Pipeline failed: {e}")
        raise

    duration_seconds = time.time() - start_time
    logging.info(f"Pipeline completed. Rows: {total_rows}, duration: {duration_seconds:.1f}s")

if __name__ == "__main__":
    run()