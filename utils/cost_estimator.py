"""
Static monthly cost estimator for the cloud pipeline infrastructure this system provisions.

Deterministic, not a live pricing API and not an LLM guess: it enumerates the FIXED footprint
each bootstrap creates and multiplies by published list prices kept here as documented constants.
Because the footprint is known, the estimate is explainable line-by-line.

Footprint is aligned to the real bootstraps:
  AWS   → EKS (Auto Mode) + RDS db.t4g.micro + S3            (bootstrap/aws)
  Azure → AKS + Standard_D2s_v6 nodes + Postgres B1ms + ADLS (bootstrap/azure)
  GCP   → GKE Autopilot (pod-billed) + Cloud SQL f1-micro + GCS (bootstrap/gcp)
  Databricks → host cloud = AWS: jobs cluster (DBUs) + serverless SQL + source RDS + S3

Honesty: list prices, representative regions, ~2026-06. EKS Auto Mode / GKE Autopilot / the
Databricks jobs cluster + serverless SQL are USAGE-billed, so the Databricks figure especially is
a typical-run estimate, not a fixed 24/7 bill. Excludes egress/data-scan. Real bills vary.

Same public API + return shape as before (estimate_monthly_cost / compare_clouds →
{cloud, items, total, last_updated, disclaimer}); the Streamlit cost panels are untouched.
"""
from __future__ import annotations

PRICES_LAST_UPDATED = "2026-06"
PRICES_DISCLAIMER = (
    "Estimates based on public list prices (~June 2026), representative regions, on-demand. "
    "EKS Auto Mode / GKE Autopilot / Databricks jobs+serverless are usage-billed — figures are "
    "a typical steady run, not a guaranteed 24/7 bill. Excludes egress/data-scan. "
    "Review official cloud pricing before infrastructure decisions."
)

_HOURS = 730  # billing month

_PRICES: dict[str, dict[str, float]] = {
    "aws": {
        "eks_control_plane":   73.0,   # $0.10/hr × 730
        "eks_automode_compute": 188.0, # 2× m5.large ($0.115/hr) + ~12% Auto Mode mgmt fee
        "rds_t4g_micro":        15.0,  # db.t4g.micro + 20 GB gp3
        "s3_gb":                 0.023,
        "ecr_gb":                0.10,
        "data_transfer_gb":      0.09,
    },
    "azure": {
        "aks_control_plane":     0.0,  # free tier
        "aks_nodes_d2s_v6":    162.0,  # 2× Standard_D2s_v6 ($0.111/hr each)
        "postgresql_b1ms":      13.0,  # B_Standard_B1ms
        "adls_gb":               0.0184,
        "acr_basic":             5.0,
        "data_transfer_gb":      0.087,
    },
    "gcp": {
        "gke_autopilot_fee":    73.0,  # $0.10/hr cluster fee (Standard/Autopilot)
        "gke_autopilot_pods":   79.0,  # Autopilot pod requests ≈ 2 vCPU + 4 GB for the stack
        "cloud_sql_f1_micro":    8.0,  # db-f1-micro
        "gcs_gb":                0.020,
        "artifact_reg_gb":       0.10,
        "data_transfer_gb":      0.08,
    },
    "databricks": {
        # Host cloud = AWS. Jobs cluster auto-terminates; SQL warehouse is serverless → usage-billed.
        "jobs_dbu_hr":           0.15,  # Jobs Compute per DBU/hr
        "jobs_dbu_count":        2.0,   # 1 driver + 1 worker m5d.xlarge ≈ 2 DBU (num_workers=1)
        "jobs_hours_day":        2.0,   # typical daily batch run
        "sql_serverless_dbu_hr": 0.70,  # Serverless SQL per DBU/hr
        "sql_dbu_count":         4.0,   # 2X-Small serverless warehouse ≈ 4 DBU
        "sql_hours_day":         1.0,   # dashboard queries
        "source_rds":           15.0,   # db.t4g.micro source (bootstrap/databricks/database.tf)
        "s3_gb":                 0.023, # DBFS + UC managed storage on S3 (AWS host)
        "data_transfer_gb":      0.09,
    },
}

_DEFAULT_STORAGE_GB  = 50
_DEFAULT_REQUESTS_K  = 100  # retained for signature stability (unused after spec alignment)
_DEFAULT_TRANSFER_GB = 10
_DEFAULT_IMAGE_GB    = 5
_DAYS_PER_MONTH      = 30


def estimate_monthly_cost(cloud: str, storage_gb: int = _DEFAULT_STORAGE_GB) -> dict:
    """Return itemised monthly cost estimate for a single cloud."""
    p = _PRICES.get(cloud)
    if not p:
        return {}

    if cloud == "aws":
        items = {
            "EKS Control Plane":                p["eks_control_plane"],
            "EKS Compute (Auto Mode, 2× m5.large)": p["eks_automode_compute"],
            "RDS PostgreSQL (db.t4g.micro)":    p["rds_t4g_micro"],
            f"S3 Storage ({storage_gb} GB)":    round(storage_gb * p["s3_gb"], 2),
            f"ECR ({_DEFAULT_IMAGE_GB} GB)":    round(_DEFAULT_IMAGE_GB * p["ecr_gb"], 2),
            "Data Transfer (10 GB out)":        round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
        }
    elif cloud == "azure":
        items = {
            "AKS Control Plane":                p["aks_control_plane"],
            "AKS Nodes (2× Standard_D2s_v6)":   p["aks_nodes_d2s_v6"],
            "PostgreSQL Flexible (B1ms)":       p["postgresql_b1ms"],
            f"ADLS Gen2 ({storage_gb} GB)":     round(storage_gb * p["adls_gb"], 2),
            "ACR Basic":                        p["acr_basic"],
            "Data Transfer (10 GB out)":        round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
        }
    elif cloud == "gcp":
        items = {
            "GKE Autopilot Cluster Fee":        p["gke_autopilot_fee"],
            "GKE Autopilot Compute (pods)":     p["gke_autopilot_pods"],
            "Cloud SQL (db-f1-micro)":          p["cloud_sql_f1_micro"],
            f"GCS Storage ({storage_gb} GB)":   round(storage_gb * p["gcs_gb"], 2),
            f"Artifact Registry ({_DEFAULT_IMAGE_GB} GB)": round(_DEFAULT_IMAGE_GB * p["artifact_reg_gb"], 2),
            "Data Transfer (10 GB out)":        round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
        }
    else:  # databricks (host cloud = AWS)
        jobs_mo = round(p["jobs_dbu_count"] * p["jobs_dbu_hr"] * p["jobs_hours_day"] * _DAYS_PER_MONTH, 2)
        sql_mo  = round(p["sql_dbu_count"] * p["sql_serverless_dbu_hr"] * p["sql_hours_day"] * _DAYS_PER_MONTH, 2)
        items = {
            f"Jobs Compute ({int(p['jobs_dbu_count'])} DBU × {int(p['jobs_hours_day'])}h/day)": jobs_mo,
            f"Serverless SQL ({int(p['sql_dbu_count'])} DBU × {int(p['sql_hours_day'])}h/day)": sql_mo,
            "Source RDS (db.t4g.micro)":         p["source_rds"],
            f"S3 (DBFS + UC managed, {storage_gb} GB)": round(storage_gb * p["s3_gb"], 2),
            "Data Transfer (10 GB out)":         round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
        }

    return {
        "cloud": cloud,
        "items": items,
        "total": round(sum(items.values()), 2),
    }


def compare_clouds(storage_gb: int = _DEFAULT_STORAGE_GB) -> list[dict]:
    """Return cost estimates for all 4 options, sorted cheapest first."""
    estimates = [estimate_monthly_cost(c, storage_gb) for c in ("aws", "azure", "gcp", "databricks")]
    for e in estimates:
        e["last_updated"] = PRICES_LAST_UPDATED
        e["disclaimer"] = PRICES_DISCLAIMER
    return sorted(estimates, key=lambda x: x["total"])


if __name__ == "__main__":
    # Single source of truth for the cost numbers quoted in docs (docs/VISION.md) and the promo
    # captions. Run with `python -m utils.cost_estimator`; docs must be regenerated from this, not
    # hand-edited.
    _rows = compare_clouds()
    print(f"Monthly cost estimate (list prices, {_DEFAULT_STORAGE_GB} GB, {PRICES_LAST_UPDATED}):\n")
    for _e in _rows:
        print(f"  {_e['cloud']:12} ~${round(_e['total']):>4}")
    print(f"\n{_rows[0]['disclaimer']}")
