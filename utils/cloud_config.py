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
# Maps (cloud, generic_key) → env-var name.
# The DB-engine-specific prefix (POSTGRES_ / MYSQL_) encodes the engine so
# different DB credentials are never mixed up.  Callers choose the right lookup
# by passing db_type to cloud_get() — see below.
#
# Convention:
#   postgres on AWS                 → POSTGRES_DB_*
#   postgres on Azure (us_crm)      → CRM_DB_*    (matches bootstrap/azure outputs,
#                                                  cicd_standards secret keys, seed_chaos.py)
#   mysql on GCP                    → MYSQL_DB_*
#   mysql on AWS/Azure              → MYSQL_DB_*   (via db_type="mysql")
_ENV_FALLBACKS: dict[tuple[str, str, str], str] = {
    # ── AWS ──────────────────────────────────────────────────────────────────
    ("aws", "postgres", "db_host"):     "POSTGRES_DB_HOST",
    ("aws", "postgres", "db_port"):     "POSTGRES_DB_PORT",
    ("aws", "postgres", "db_user"):     "POSTGRES_DB_USER",
    ("aws", "postgres", "db_password"): "POSTGRES_DB_PASSWORD",
    ("aws", "postgres", "db_name"):     "POSTGRES_DB_NAME",
    ("aws", "mysql",    "db_host"):     "MYSQL_DB_HOST",
    ("aws", "mysql",    "db_port"):     "MYSQL_DB_PORT",
    ("aws", "mysql",    "db_user"):     "MYSQL_DB_USER",
    ("aws", "mysql",    "db_password"): "MYSQL_DB_PASSWORD",
    ("aws", "mysql",    "db_name"):     "MYSQL_DB_NAME",
    # ── GCP ──────────────────────────────────────────────────────────────────
    ("gcp", "postgres", "db_host"):     "POSTGRES_DB_HOST",
    ("gcp", "postgres", "db_port"):     "POSTGRES_DB_PORT",
    ("gcp", "postgres", "db_user"):     "POSTGRES_DB_USER",
    ("gcp", "postgres", "db_password"): "POSTGRES_DB_PASSWORD",
    ("gcp", "postgres", "db_name"):     "POSTGRES_DB_NAME",
    ("gcp", "mysql",    "db_host"):     "MYSQL_DB_HOST",
    ("gcp", "mysql",    "db_port"):     "MYSQL_DB_PORT",
    ("gcp", "mysql",    "db_user"):     "MYSQL_DB_USER",
    ("gcp", "mysql",    "db_password"): "MYSQL_DB_PASSWORD",
    ("gcp", "mysql",    "db_name"):     "MYSQL_DB_NAME",
    # ── Azure ─────────────────────────────────────────────────────────────────
    # Azure has no SSM; the K8s secret is populated from GitHub vars/secrets under the
    # CRM_DB_* names (see bootstrap/azure/outputs.tf, cicd_standards.md, seed_chaos.py).
    ("azure", "postgres", "db_host"):     "CRM_DB_HOST",
    ("azure", "postgres", "db_port"):     "CRM_DB_PORT",
    ("azure", "postgres", "db_user"):     "CRM_DB_USER",
    ("azure", "postgres", "db_password"): "CRM_DB_PASSWORD",
    ("azure", "postgres", "db_name"):     "CRM_DB_NAME",
    ("azure", "mysql",    "db_host"):     "MYSQL_DB_HOST",
    ("azure", "mysql",    "db_port"):     "MYSQL_DB_PORT",
    ("azure", "mysql",    "db_user"):     "MYSQL_DB_USER",
    ("azure", "mysql",    "db_password"): "MYSQL_DB_PASSWORD",
    ("azure", "mysql",    "db_name"):     "MYSQL_DB_NAME",
}

# ── Backward-compatibility ────────────────────────────────────────────────────
# Maps old caller-facing key names to the new generic key (for API callers
# that still pass rds_host etc.).  Applied ONLY for env-var lookup so it
# does NOT change the SSM parameter path — old SSM params stay reachable.
_LEGACY_KEY_MAP: dict[str, str] = {
    "rds_host":     "db_host",
    "rds_port":     "db_port",
    "rds_username": "db_user",
    "rds_password": "db_password",
    "rds_db_name":  "db_name",
}

# SSM parameter name candidates for each generic key, tried in order.
# Allows reading params that were written under the old naming convention
# (rds_host, rds_username …) without migrating them.
_SSM_KEY_CANDIDATES: dict[str, list[str]] = {
    "db_host":     ["db_host",     "rds_host"],
    "db_port":     ["db_port",     "rds_port"],
    "db_user":     ["db_user",     "rds_username"],
    "db_password": ["db_password", "rds_password"],
    "db_name":     ["db_name",     "rds_db_name"],
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

def cloud_get(cloud: str, key: str, db_type: str = "postgres", *, use_ssm: bool = True) -> Optional[str]:
    """
    Retrieve a config value using the three-tier priority chain.

    Args:
        cloud:    "aws" | "gcp" | "azure"
        key:      Generic key — db_host | db_port | db_user | db_password | db_name.
                  Legacy RDS-specific keys (rds_host, rds_username, rds_db_name) are
                  transparently resolved via _LEGACY_ALIASES for backward compatibility.
        db_type:  "postgres" | "mysql" — selects the correct env-var fallback so
                  credentials for different engines are never mixed up.
        use_ssm:  Set False in unit tests to skip the live SSM call.

    Returns:
        The config value as a string, or None if not found anywhere.
    """
    # Normalise legacy caller-facing key names to the generic equivalent.
    generic_key = _LEGACY_KEY_MAP.get(key, key)

    # ── 1. SSM (AWS only) ─────────────────────────────────────────────────────
    # Try generic key first (new style), then legacy names — so existing SSM
    # parameters written as /aws/rds_host still work without migration.
    if use_ssm and cloud == "aws":
        for ssm_key in _SSM_KEY_CANDIDATES.get(generic_key, [generic_key]):
            value = _try_ssm(cloud, ssm_key)
            if value is not None:
                logger.debug(f"📦 SSM: {cloud}/{ssm_key}")
                return value

    # ── 2. .bootstrap_outputs.json ────────────────────────────────────────────
    # Try generic key first, then the original key (covers old bootstrap files).
    outputs = _load_bootstrap_outputs()
    for bk in ([generic_key] if generic_key == key else [generic_key, key]):
        value = outputs.get(cloud, {}).get(bk)
        if value is not None:
            logger.debug(f"📄 bootstrap_outputs: {cloud}/{bk}")
            return str(value)

    # ── 3. Environment variable ───────────────────────────────────────────────
    # Use the (cloud, db_type, generic_key) triple so postgres and mysql
    # credentials are never confused.
    env_var = _ENV_FALLBACKS.get((cloud, db_type, generic_key))
    if env_var:
        value = os.getenv(env_var)
        if value:
            logger.debug(f"🔑 env var: {env_var}")
            return value

    logger.warning(f"⚠️  No value found for {cloud}/{db_type}/{key}")
    return None


def cloud_get_infra(cloud: str, key: str, *, use_ssm: bool = True) -> Optional[str]:
    """
    Retrieve a NON-credential infrastructure value (e.g. ecr_repository_url,
    eks_cluster_name) using the same source chain as cloud_get, minus the
    DB-engine env-var table.

    Source order:
      1. AWS SSM Parameter Store (AWS only) — written by bootstrap terraform apply.
      2. .bootstrap_outputs.json — local dev fallback.

    These values are written to SSM by the bootstrap phase (see bootstrap/aws/ssm.tf)
    and read here by whoever needs them (the infra agent). Single source of truth:
    the bootstrap that creates the resource also publishes its identifier.

    Returns the value as a string, or None if not found.
    """
    if use_ssm and cloud == "aws":
        value = _try_ssm(cloud, key)
        if value is not None:
            logger.debug(f"📦 SSM (infra): {cloud}/{key}")
            return value

    outputs = _load_bootstrap_outputs()
    value = outputs.get(cloud, {}).get(key)
    if value is not None:
        logger.debug(f"📄 bootstrap_outputs (infra): {cloud}/{key}")
        return str(value)

    logger.warning(f"⚠️  No infra value found for {cloud}/{key}")
    return None
