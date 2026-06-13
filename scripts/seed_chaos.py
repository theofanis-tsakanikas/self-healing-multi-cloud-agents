import os
import sys
import argparse
import logging
import numpy as np
import pandas as pd
from faker import Faker
from sqlalchemy import create_engine
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.cloud_config import cloud_get

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load Environment Variables
env_path = os.path.join(os.getcwd(), '.env')
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
    logger.info(f"Loaded environment variables from: {env_path}")

fake = Faker()

def create_chaos_data(target, n_rows=100):
    """
    Generates a DataFrame with intentional anomalies for a specific target only.
    This saves memory by not generating unused data.
    """
    logger.info(f"Generating chaos data for target: {target}")

    if target in ("eu_sales", "sales_lakehouse"):
        # sales_lakehouse reuses the EU-Sales schema (same business rules) but lands in its
        # OWN dedicated RDS / table — see build_engine + raw_{target}.
        data_list = []
        for _ in range(n_rows):
            data_list.append({
                "order_id": fake.uuid4(),
                "unit_price": np.random.choice([fake.pyfloat(left_digits=2, right_digits=2, positive=True), -10.0, 0.0, None], p=[0.8, 0.05, 0.05, 0.1]),
                "quantity": np.random.choice([np.random.randint(1, 10), -1, None], p=[0.8, 0.1, 0.1]),
                "order_date": np.random.choice([fake.date_this_year(), "2030-01-01", None], p=[0.8, 0.1, 0.1]),
                "currency": np.random.choice(["EUR", "GBP", "XYZ", None], p=[0.7, 0.1, 0.1, 0.1]),
            })
        return pd.DataFrame(data_list)

    elif target == "us_crm":
        data_list = []
        for _ in range(n_rows):
            data_list.append({
                "cust_id": np.random.choice([fake.unique.random_int(1000, 9999), 1000], p=[0.95, 0.05]),
                "full_name": fake.name(),
                "email_address": np.random.choice([fake.email(), "invalid_email.com", None], p=[0.8, 0.1, 0.1]),
                "phone_number": np.random.choice([fake.phone_number(), "123", None], p=[0.8, 0.1, 0.1]),
            })
        return pd.DataFrame(data_list)

    elif target == "global_marketing":
        data_list = []
        platforms = ["Google_Ads", "FB", "Instagram", "Meta", "TikTok"]
        for _ in range(n_rows):
            data_list.append({
                "campaign_id": fake.bothify(text="CMP-####"),
                "platform_name": np.random.choice(platforms),
                "ad_spend": np.random.choice([fake.pyfloat(left_digits=3, right_digits=2, positive=True), "not_a_number", None], p=[0.8, 0.1, 0.1]),
                "clicks": np.random.randint(0, 1000),
                # impressions: normally >> clicks; the small-value chaos makes clicks > impressions
                # so the engagement_logic_check FLAG_AS_SUSPICIOUS rule has rows to flag.
                "impressions": np.random.choice([np.random.randint(1000, 50000), np.random.randint(0, 50), None], p=[0.8, 0.1, 0.1]),
                # event_timestamp: the future-dated chaos trips the temporal_validity rule.
                "event_timestamp": np.random.choice([fake.date_time_this_year(), fake.future_datetime(end_date="+2y"), None], p=[0.8, 0.1, 0.1]),
            })
        return pd.DataFrame(data_list)

    return pd.DataFrame()

def build_engine(db_type, target):
    """
    Dynamically creates a SQLAlchemy engine.
    Each target maps to its own cloud database with dedicated env var prefixes:
      eu_sales        → AWS RDS PostgreSQL       → POSTGRES_DB_*
      us_crm          → Azure PostgreSQL          → CRM_DB_*
      global_marketing→ GCP Cloud SQL MySQL       → MYSQL_DB_*
    """
    if db_type == "sqlite":
        db_path = os.path.join("data", f"{target}_raw.db")
        return create_engine(f"sqlite:///{db_path}")

    # Target-specific credential prefix — each cloud has its own DB secrets
    CREDENTIAL_MAP = {
        "eu_sales":         {"prefix": "POSTGRES", "driver": "postgresql+psycopg2", "default_port": "5432"},
        "sales_lakehouse":  {"prefix": "POSTGRES", "driver": "postgresql+psycopg2", "default_port": "5432"},
        "us_crm":           {"prefix": "CRM",      "driver": "postgresql+psycopg2", "default_port": "5432"},
        "global_marketing": {"prefix": "MYSQL",    "driver": "mysql+pymysql",       "default_port": "3306"},
    }

    if target in CREDENTIAL_MAP:
        cfg = CREDENTIAL_MAP[target]
        driver = cfg["driver"]
        default_port = cfg["default_port"]

        # eu_sales → AWS RDS via SSM; global_marketing → GCP env fallback
        if target == "eu_sales":
            host    = cloud_get("aws", "rds_host")
            port    = cloud_get("aws", "rds_port") or default_port
            user    = cloud_get("aws", "rds_username")
            password = cloud_get("aws", "rds_password")
            db_name = cloud_get("aws", "rds_db_name")
        elif target == "sales_lakehouse":
            # Databricks source RDS — its own SSM keys (lakehouse_db_*), same as the AWS
            # 3-tier pattern. No POSTGRES_DB_* env / .env dependency.
            host    = cloud_get("aws", "lakehouse_db_host")
            port    = cloud_get("aws", "lakehouse_db_port") or default_port
            user    = cloud_get("aws", "lakehouse_db_user")
            password = cloud_get("aws", "lakehouse_db_password")
            db_name = cloud_get("aws", "lakehouse_db_name")
        elif target == "global_marketing":
            # GCP source is Cloud SQL for MySQL → db_type="mysql" so cloud_get resolves the
            # MYSQL_DB_* env fallbacks (not the POSTGRES_DB_* default), matching the prefix above.
            host    = cloud_get("gcp", "db_host",     db_type="mysql")
            port    = cloud_get("gcp", "db_port",     db_type="mysql") or default_port
            user    = cloud_get("gcp", "db_user",     db_type="mysql")
            password = cloud_get("gcp", "db_password", db_type="mysql")
            db_name = cloud_get("gcp", "db_name",     db_type="mysql")
        else:  # us_crm (Azure) — env var fallback until Azure Secret Manager is set up
            prefix = cfg["prefix"]
            host    = os.getenv(f"{prefix}_DB_HOST")
            port    = os.getenv(f"{prefix}_DB_PORT", default_port)
            user    = os.getenv(f"{prefix}_DB_USER")
            password = os.getenv(f"{prefix}_DB_PASSWORD")
            db_name = os.getenv(f"{prefix}_DB_NAME")

        # URL-encode user/password: a special char (@ : / # %) in the password otherwise
        # breaks SQLAlchemy URL parsing and the wrong password is sent → "Access denied".
        url = f"{driver}://{quote_plus(user or '')}:{quote_plus(password or '')}@{host}:{port}/{db_name}"
        return create_engine(url)

    # Fallback for explicit db_type (backward compat)
    if db_type == "postgres":
        url = f"postgresql+psycopg2://{cloud_get('aws','rds_username')}:{cloud_get('aws','rds_password')}@{cloud_get('aws','rds_host')}:{cloud_get('aws','rds_port') or '5432'}/{cloud_get('aws','rds_db_name')}"
        return create_engine(url)

    if db_type == "mysql":
        url = f"mysql+pymysql://{cloud_get('gcp','db_user',db_type='mysql')}:{cloud_get('gcp','db_password',db_type='mysql')}@{cloud_get('gcp','db_host',db_type='mysql')}:{cloud_get('gcp','db_port',db_type='mysql') or '3306'}/{cloud_get('gcp','db_name',db_type='mysql')}"
        return create_engine(url)

    raise ValueError(f"Unsupported DB type: {db_type}")


# Each NL cloud reads from the SAME source DB as its validated pipeline (the agent resolves
# creds via that pipeline's SSM/env wiring), so seed a user sample onto that cloud's source
# DB by reusing the validated target's connection in build_engine.
_CLOUD_SOURCE_TARGET = {"aws": "eu_sales", "azure": "us_crm", "gcp": "global_marketing",
                        "databricks": "sales_lakehouse"}


def seed_dataframe_to_source(df: pd.DataFrame, slug: str, cloud: str = "aws") -> str:
    """
    Load a user-supplied sample ("bring your own data" via the NL/Streamlit surface) into the
    cloud's source database as `raw_<slug>`, so a runtime-authored pipeline whose source_table
    is `raw_<slug>` has a real table to extract at deploy time. Reuses build_engine's per-cloud
    connection via the cloud's validated source target (AWS→eu_sales RDS, Azure→us_crm
    PostgreSQL, GCP→global_marketing MySQL). Returns the table name written.
    """
    if df is None or df.empty:
        raise ValueError("Empty dataset — nothing to seed into the source database.")
    target = _CLOUD_SOURCE_TARGET.get(cloud)
    if target is None:
        raise ValueError(f"Unsupported cloud for seeding: {cloud!r} (aws|azure|gcp).")
    db_type = "mysql" if cloud == "gcp" else "postgres"
    engine = build_engine(db_type, target)
    table_name = f"raw_{slug}"
    with engine.connect():  # fail fast on a bad connection before to_sql
        logger.info(f"Connection OK ({cloud}/{db_type}); writing sample to '{table_name}'…")
    df.to_sql(name=table_name, con=engine, if_exists="replace", index=False)
    logger.info(f"✅ Seeded {len(df)} rows into source table '{table_name}' ({cloud})")
    return table_name


def seed_chaos(target_arg, db_type, n_rows):
    available_targets = ["eu_sales", "us_crm", "global_marketing"]
    targets_to_run = available_targets if target_arg == "all" else [target_arg]

    for target in targets_to_run:
        try:
            df = create_chaos_data(target, n_rows=n_rows)

            if not df.empty:
                engine = build_engine(db_type, target)
                table_name = f"raw_{target}"

                # TEST CONNECTION: If it doesn't connect here, it will go to except
                with engine.connect():   # connection test only — failure routes to except
                    logger.info(f"Connection successful to {db_type} for target={target}")

                logger.info(f"🔥 Injecting {n_rows} chaotic rows into '{table_name}'...")

                df.to_sql(
                    name=table_name,
                    con=engine,
                    if_exists="replace",
                    index=False
                )
                logger.info(f"✅ Successfully seeded '{table_name}'")
            else:
                logger.warning(f"⚠️ No data generated for target {target}")

        except Exception as e:
            logger.error(f"❌ FATAL ERROR for {target}: {str(e)}")
            # Stop execution to see the error in GitHub Actions
            exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Advanced Chaos Engineering Engine")

    parser.add_argument(
        "--target",
        choices=["eu_sales", "sales_lakehouse", "us_crm", "global_marketing", "all"],
        default="all",
        help="The functional scope of the data"
    )

    parser.add_argument(
        "--db-type",
        choices=["sqlite", "postgres", "mysql"],
        default="postgres",
        help="Database engine to use"
    )

    parser.add_argument(
        "--rows",
        type=int,
        default=100,
        help="Number of rows to generate per table"
    )

    args = parser.parse_args()

    os.makedirs("data", exist_ok=True)

    logger.info(f"🚀 STARTING CHAOS: Scope={args.target}, DB={args.db_type}, Rows={args.rows}")
    seed_chaos(args.target, args.db_type, args.rows)
    logger.info("✨ Process Completed.")
