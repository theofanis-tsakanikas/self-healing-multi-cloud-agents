"""
Unified configuration loader.

Priority chain (highest → lowest):
  1. AWS SSM Parameter Store   — production runtime (auto-populated by terraform apply)
  2. .bootstrap_outputs.json   — local dev (populated by scripts/export_bootstrap_outputs.py)
  3. Environment variables      — CI/CD, Docker, or last resort

Convention for SSM parameter names:
  /multi-cloud-self-healing-agent/<cloud>/<key>
  e.g.  /multi-cloud-self-healing-agent/aws/rds_host

Usage:
    from utils.cloud_config import cloud_get

    host = cloud_get("aws", "rds_host")        # returns str or None
    pw   = cloud_get("aws", "rds_password")    # decrypts SecureString transparently
"""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BOOTSTRAP_FILE = _PROJECT_ROOT / ".bootstrap_outputs.json"
_SSM_PREFIX = "/multi-cloud-self-healing-agent"

# ── Env-var fallback table ────────────────────────────────────────────────────
# Maps (cloud, key) → environment variable name used as last resort.
_ENV_FALLBACKS: dict[tuple[str, str], str] = {
    # AWS / RDS (PostgreSQL)
    ("aws", "rds_host"):      "POSTGRES_DB_HOST",
    ("aws", "rds_port"):      "POSTGRES_DB_PORT",
    ("aws", "rds_username"):  "POSTGRES_DB_USER",
    ("aws", "rds_password"):  "POSTGRES_DB_PASSWORD",
    ("aws", "rds_db_name"):   "POSTGRES_DB_NAME",
    # GCP / Cloud SQL (MySQL)
    ("gcp", "db_host"):       "MYSQL_DB_HOST",
    ("gcp", "db_port"):       "MYSQL_DB_PORT",
    ("gcp", "db_user"):       "MYSQL_DB_USER",
    ("gcp", "db_password"):   "MYSQL_DB_PASSWORD",
    ("gcp", "db_name"):       "MYSQL_DB_NAME",
}


# ── Loaders ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_bootstrap_outputs() -> dict:
    """Load .bootstrap_outputs.json once and cache in process memory."""
    if _BOOTSTRAP_FILE.exists():
        with open(_BOOTSTRAP_FILE) as f:
            data = json.load(f)
        logger.info("✅ Bootstrap outputs loaded from .bootstrap_outputs.json")
        return data
    logger.warning(
        "⚠️  .bootstrap_outputs.json not found — "
        "run `python scripts/export_bootstrap_outputs.py` after bootstrap."
    )
    return {}


def _try_ssm(cloud: str, key: str) -> Optional[str]:
    """
    Attempt to read from AWS SSM Parameter Store.
    Returns None gracefully if boto3 is unavailable or credentials are missing.
    Only called for cloud == "aws".
    """
    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError, NoRegionError

        region = os.getenv("AWS_DEFAULT_REGION", "eu-central-1")
        ssm = boto3.client("ssm", region_name=region)
        param_name = f"{_SSM_PREFIX}/{cloud}/{key}"
        response = ssm.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]

    except ImportError:
        logger.debug("boto3 not installed — SSM lookup skipped.")
        return None
    except Exception as exc:
        # NoCredentialsError, ClientError (ParameterNotFound), etc.
        logger.debug(f"SSM lookup skipped for {cloud}/{key}: {exc}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def cloud_get(cloud: str, key: str, *, use_ssm: bool = True) -> Optional[str]:
    """
    Retrieve a config value using the three-tier priority chain.

    Args:
        cloud:    "aws" | "gcp" | "azure"
        key:      Key as it appears in bootstrap_outputs.json (e.g. "rds_host")
        use_ssm:  Set False in unit tests to skip the live SSM call.

    Returns:
        The config value as a string, or None if not found anywhere.
    """
    # ── 1. SSM (AWS only) ─────────────────────────────────────────────────────
    if use_ssm and cloud == "aws":
        value = _try_ssm(cloud, key)
        if value is not None:
            logger.debug(f"📦 SSM: {cloud}/{key}")
            return value

    # ── 2. .bootstrap_outputs.json ────────────────────────────────────────────
    outputs = _load_bootstrap_outputs()
    value = outputs.get(cloud, {}).get(key)
    if value is not None:
        logger.debug(f"📄 bootstrap_outputs: {cloud}/{key}")
        return str(value)

    # ── 3. Environment variable ───────────────────────────────────────────────
    env_var = _ENV_FALLBACKS.get((cloud, key))
    if env_var:
        value = os.getenv(env_var)
        if value:
            logger.debug(f"🔑 env var: {env_var}")
            return value

    logger.warning(f"⚠️  No value found for {cloud}/{key}")
    return None
