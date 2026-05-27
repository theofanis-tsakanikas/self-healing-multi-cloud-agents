"""
Unified configuration loader.

Priority chain (highest → lowest):
  1. AWS SSM Parameter Store   — production runtime (auto-populated by terraform apply)
  2. .bootstrap_outputs.json   — local dev (populated by scripts/export_bootstrap_outputs.py)
  3. Environment variables      — CI/CD, Docker, or last resort

Convention for SSM parameter names:
  /multi-cloud-self-healing-agent/<cloud>/<key>
  e.g.  /multi-cloud-self-healing-agent/aws/db_host

Keys are GENERIC and DB-engine-agnostic: db_host, db_port, db_user, db_password, db_name.
The same API works for any combination of cloud provider and database engine:

    cloud_get("aws",   "db_host")   # PostgreSQL or MySQL on AWS RDS
    cloud_get("gcp",   "db_host")   # PostgreSQL or MySQL on GCP Cloud SQL
    cloud_get("azure", "db_host")   # PostgreSQL or MySQL on Azure Database

For the env-var fallback (tier 3), both POSTGRES_DB_* and MYSQL_DB_* are tried
in order, so whichever the operator has set in their environment will be found.

Backward-compatibility aliases (rds_host etc.) are retained so existing
.bootstrap_outputs.json files and SSM parameters written before this refactor
keep working — cloud_get() checks the alias automatically.
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
# Maps (cloud, generic_key) → list of env-var names tried in order.
# Both POSTGRES_DB_* and MYSQL_DB_* are listed so the function works regardless
# of which DB engine the operator has configured.
_ENV_FALLBACKS: dict[tuple[str, str], list[str]] = {
    # ── AWS (RDS: PostgreSQL, MySQL, or any other engine) ────────────────────
    ("aws", "db_host"):     ["POSTGRES_DB_HOST",     "MYSQL_DB_HOST"],
    ("aws", "db_port"):     ["POSTGRES_DB_PORT",     "MYSQL_DB_PORT"],
    ("aws", "db_user"):     ["POSTGRES_DB_USER",     "MYSQL_DB_USER"],
    ("aws", "db_password"): ["POSTGRES_DB_PASSWORD", "MYSQL_DB_PASSWORD"],
    ("aws", "db_name"):     ["POSTGRES_DB_NAME",     "MYSQL_DB_NAME"],
    # ── GCP (Cloud SQL: PostgreSQL or MySQL) ──────────────────────────────────
    ("gcp", "db_host"):     ["POSTGRES_DB_HOST",     "MYSQL_DB_HOST"],
    ("gcp", "db_port"):     ["POSTGRES_DB_PORT",     "MYSQL_DB_PORT"],
    ("gcp", "db_user"):     ["POSTGRES_DB_USER",     "MYSQL_DB_USER"],
    ("gcp", "db_password"): ["POSTGRES_DB_PASSWORD", "MYSQL_DB_PASSWORD"],
    ("gcp", "db_name"):     ["POSTGRES_DB_NAME",     "MYSQL_DB_NAME"],
    # ── Azure (Azure Database for PostgreSQL / MySQL) ─────────────────────────
    ("azure", "db_host"):     ["POSTGRES_DB_HOST",     "MYSQL_DB_HOST"],
    ("azure", "db_port"):     ["POSTGRES_DB_PORT",     "MYSQL_DB_PORT"],
    ("azure", "db_user"):     ["POSTGRES_DB_USER",     "MYSQL_DB_USER"],
    ("azure", "db_password"): ["POSTGRES_DB_PASSWORD", "MYSQL_DB_PASSWORD"],
    ("azure", "db_name"):     ["POSTGRES_DB_NAME",     "MYSQL_DB_NAME"],
}

# ── Backward-compatibility aliases ────────────────────────────────────────────
# Old keys written by bootstrap scripts or stored in SSM before the generic
# naming convention was introduced.  cloud_get() resolves these transparently.
_LEGACY_ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    ("aws", "rds_host"):     ("aws", "db_host"),
    ("aws", "rds_port"):     ("aws", "db_port"),
    ("aws", "rds_username"): ("aws", "db_user"),
    ("aws", "rds_password"): ("aws", "db_password"),
    ("aws", "rds_db_name"):  ("aws", "db_name"),
    ("gcp", "db_user"):      ("gcp", "db_user"),   # already generic, no-op
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

        region = os.getenv("AWS_DEFAULT_REGION", "eu-central-1")
        ssm = boto3.client("ssm", region_name=region)
        param_name = f"{_SSM_PREFIX}/{cloud}/{key}"
        response = ssm.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]

    except ImportError:
        logger.debug("boto3 not installed — SSM lookup skipped.")
        return None
    except Exception as exc:
        logger.debug(f"SSM lookup skipped for {cloud}/{key}: {exc}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def cloud_get(cloud: str, key: str, *, use_ssm: bool = True) -> Optional[str]:
    """
    Retrieve a config value using the three-tier priority chain.

    Args:
        cloud:    "aws" | "gcp" | "azure"
        key:      Generic key — db_host | db_port | db_user | db_password | db_name.
                  Legacy RDS-specific keys (rds_host, rds_username, rds_db_name) are
                  transparently resolved via _LEGACY_ALIASES for backward compatibility.
        use_ssm:  Set False in unit tests to skip the live SSM call.

    Returns:
        The config value as a string, or None if not found anywhere.
    """
    # Resolve legacy key aliases transparently
    cloud, key = _LEGACY_ALIASES.get((cloud, key), (cloud, key))

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

    # ── 3. Environment variables (try each candidate in order) ────────────────
    for env_var in _ENV_FALLBACKS.get((cloud, key), []):
        value = os.getenv(env_var)
        if value:
            logger.debug(f"🔑 env var: {env_var}")
            return value

    logger.warning(f"⚠️  No value found for {cloud}/{key}")
    return None
