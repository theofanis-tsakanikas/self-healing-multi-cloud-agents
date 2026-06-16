"""
Reads terraform outputs from each bootstrap directory and merges them into
a single .bootstrap_outputs.json file at the project root.

Run this ONCE after each `terraform apply` in bootstrap/:

    python scripts/export_bootstrap_outputs.py        # all clouds
    python scripts/export_bootstrap_outputs.py aws    # single cloud
    python scripts/export_bootstrap_outputs.py azure
    python scripts/export_bootstrap_outputs.py gcp
"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_DIR = PROJECT_ROOT / "bootstrap"
OUTPUT_FILE = PROJECT_ROOT / ".bootstrap_outputs.json"


# ---------------------------------------------------------------------------
# Core: run terraform output -json
# ---------------------------------------------------------------------------

def _terraform_output(cloud: str) -> dict:
    tf_dir = BOOTSTRAP_DIR / cloud
    if not tf_dir.exists():
        print(f"  ⚠️  bootstrap/{cloud}/ not found — skipping.")
        return {}

    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=str(tf_dir),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ❌ terraform output failed for {cloud}:\n{result.stderr[:500]}")
        return {}

    raw = json.loads(result.stdout)
    # terraform output -json wraps each value: {"key": {"value": ..., "type": ...}}
    return {k: v["value"] for k, v in raw.items()}


# ---------------------------------------------------------------------------
# Normalizers: map Terraform output keys → canonical keys used by nlp_parser
# ---------------------------------------------------------------------------

def _ecr_base_url(full_url: str) -> str:
    """'123456789.dkr.ecr.eu-central-1.amazonaws.com/repo' → base URL without repo."""
    return full_url.rsplit("/", 1)[0]


def _normalize_aws(raw: dict) -> dict:
    out = {}
    if v := raw.get("state_bucket_name"):
        out["state_bucket"] = v
    if v := raw.get("lock_table_name"):
        out["lock_table"] = v
    if v := raw.get("ecr_repository_url"):
        out["ecr_repository_url"] = v
        out["ecr_base_url"] = _ecr_base_url(v)
    if v := raw.get("eks_cluster_name"):
        out["eks_cluster_name"] = v
    if v := raw.get("eks_oidc_issuer"):
        out["eks_oidc_issuer"] = v
    if v := raw.get("irsa_role_arn"):
        out["irsa_role_arn"] = v
    # NL/Streamlit-authored pipelines: account id + the shared IRSA role they assume.
    for k in ("aws_account_id", "oidc_provider_arn",
              "pipeline_irsa_role_name", "pipeline_irsa_role_arn"):
        if v := raw.get(k):
            out[k] = v
    for k in ("rds_host", "rds_port", "rds_db_name", "rds_username"):
        if v := raw.get(k):
            out[k] = v
    return out


def _normalize_azure(raw: dict) -> dict:
    out = {}
    if v := raw.get("acr_login_server"):
        out["acr_login_server"] = v
    if v := raw.get("aks_cluster_name"):
        out["aks_cluster_name"] = v
    if v := raw.get("aks_oidc_issuer_url"):
        out["aks_oidc_issuer_url"] = v
    if v := raw.get("crm_managed_identity_client_id"):
        out["managed_identity_client_id"] = v
    # NL/Streamlit-authored pipelines: the shared managed identity they federate to.
    if v := raw.get("pipeline_managed_identity_name"):
        out["pipeline_managed_identity_name"] = v
    if v := raw.get("pipeline_managed_identity_client_id"):
        out["pipeline_managed_identity_client_id"] = v
    if v := raw.get("tfstate_storage_account"):
        out["state_storage_account"] = v
    if v := raw.get("tfstate_container_name"):
        out["state_container"] = v
    if v := raw.get("resource_group_name"):
        out["resource_group_name"] = v
    for k in ("db_host", "db_port", "db_name"):
        if v := raw.get(k):
            out[k] = v
    # The azure bootstrap output is named `db_username`, but on azure cloud_get has no
    # SSM tier and resolves the GENERIC key `db_user` from this bridge file. Emit
    # `db_user` so a local NL deploy resolves the username from the bridge (only the
    # password must come from env). Keep `db_username` too (additive — no other reader breaks).
    if v := raw.get("db_username"):
        out["db_user"] = v
        out["db_username"] = v
    return out


def _normalize_gcp(raw: dict) -> dict:
    out = {}
    if v := raw.get("artifact_registry_url"):
        out["artifact_registry_url"] = v
    if v := raw.get("gke_cluster_name"):
        out["gke_cluster_name"] = v
    if v := raw.get("marketing_service_account_email"):
        out["service_account_email"] = v
    # NL/Streamlit-authored pipelines: the shared SA they impersonate via Workload Identity.
    if v := raw.get("pipeline_service_account_email"):
        out["pipeline_service_account_email"] = v
    if v := raw.get("pipeline_service_account_id"):
        out["pipeline_service_account_id"] = v
    if v := raw.get("tfstate_bucket_name"):
        out["state_bucket"] = v
    if v := raw.get("project_id"):
        out["project_id"] = v
    if v := raw.get("region"):
        out["region"] = v
    wp = raw.get("workload_identity_provider", "")
    if wp and wp != "not configured":
        out["workload_identity_provider"] = wp
    for k in ("db_host", "db_port", "db_name", "db_user"):
        if v := raw.get(k):
            out[k] = v
    return out


def _normalize_databricks(raw: dict) -> dict:
    out = {}
    # NL-authored Databricks pipelines derive UC catalog + warehouse + source endpoint from these.
    for k in ("workspace_url", "workspace_id", "cluster_id",
              "warehouse_id", "catalog_name", "source_db_endpoint"):
        if v := raw.get(k):
            out[k] = v
    return out


_NORMALIZERS = {
    "aws": _normalize_aws,
    "azure": _normalize_azure,
    "gcp": _normalize_gcp,
    "databricks": _normalize_databricks,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def export(clouds: list):
    # Load existing file so we only overwrite the clouds being updated
    existing = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            existing = json.load(f)

    for cloud in clouds:
        print(f"\n🔍 Reading terraform outputs for {cloud.upper()}...")
        raw = _terraform_output(cloud)
        if not raw:
            continue
        normalized = _NORMALIZERS[cloud](raw)
        existing[cloud] = normalized
        print(f"  ✅ {len(normalized)} values captured.")
        for k, v in normalized.items():
            display = str(v)[:60] + "..." if len(str(v)) > 60 else v
            print(f"     {k}: {display}")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(existing, f, indent=2)

    print(f"\n✅ Saved to {OUTPUT_FILE}\n")


if __name__ == "__main__":
    all_clouds = ["aws", "azure", "gcp", "databricks"]
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    clouds = all_clouds if target == "all" else [target]
    export(clouds)
