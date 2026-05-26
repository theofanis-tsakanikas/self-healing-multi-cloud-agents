"""
Static monthly cost estimator for cloud pipeline infrastructure.
Uses list prices (not spot/reserved) as conservative upper bounds.
Sources: AWS/Azure/GCP pricing pages, eu-central-1 / westeurope / europe-west3
Databricks: Premium tier DBU pricing (Azure backend default)
"""
from __future__ import annotations

PRICES_LAST_UPDATED = "2026-05"
PRICES_DISCLAIMER = (
    "Estimates based on public list prices as of May 2026. "
    "Actual costs vary by region, commitment type, and usage patterns. "
    "Review official cloud pricing pages before making infrastructure decisions."
)

_PRICES: dict[str, dict[str, float]] = {
    "aws": {
        "eks_control_plane":  72.0,   # $0.10/hr × 720 h
        "eks_node_t3_medium": 30.0,   # 1 on-demand node
        "rds_t3_micro":       15.0,
        "s3_gb":               0.023,
        "s3_requests_k":       0.004,
        "ecr_gb":              0.10,
        "data_transfer_gb":    0.09,
    },
    "azure": {
        "aks_control_plane":   0.0,   # free tier
        "aks_node_b2ms":      35.0,   # 1 on-demand node
        "postgresql_flex_b":  26.0,
        "adls_gb":             0.018,
        "adls_ops_k":          0.004,
        "acr_basic":           5.0,
        "data_transfer_gb":    0.087,
    },
    "gcp": {
        "gke_control_plane":  72.0,   # $0.10/hr (Standard mode)
        "gke_node_n2_std2":   25.0,   # 1 preemptible node
        "cloud_sql_g1":       10.0,
        "gcs_gb":              0.020,
        "gcs_ops_k":           0.004,
        "artifact_reg_gb":     0.10,
        "data_transfer_gb":    0.08,
    },
    "databricks": {
        # DBU pricing (Premium tier, Azure backend)
        "jobs_dbu_hr":          0.15,   # Jobs Compute per DBU/hr
        "sql_dbu_hr":           0.22,   # SQL Warehouse per DBU/hr
        "jobs_dbu_count":       4.0,    # 2 workers + driver ≈ 4 DBU
        "jobs_hours_day":       2.0,    # typical daily batch run
        "sql_dbu_count":        2.0,    # XSmall SQL warehouse
        "sql_hours_day":        1.0,    # analytics queries
        "delta_storage_gb":     0.018,  # ADLS Gen2 backend
        "unity_catalog":        0.0,    # included in Premium
        "data_transfer_gb":     0.087,
    },
}

_DEFAULT_STORAGE_GB  = 50
_DEFAULT_REQUESTS_K  = 100
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
            "EKS Control Plane":           p["eks_control_plane"],
            "EKS Worker Node (t3.medium)": p["eks_node_t3_medium"],
            "RDS PostgreSQL (t3.micro)":   p["rds_t3_micro"],
            f"S3 Storage ({storage_gb} GB)":       round(storage_gb * p["s3_gb"], 2),
            "S3 Requests (100k)":          round(_DEFAULT_REQUESTS_K * p["s3_requests_k"], 2),
            f"ECR ({_DEFAULT_IMAGE_GB} GB)": round(_DEFAULT_IMAGE_GB * p["ecr_gb"], 2),
            "Data Transfer (10 GB out)":   round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
        }
    elif cloud == "azure":
        items = {
            "AKS Control Plane":              p["aks_control_plane"],
            "AKS Worker Node (B2ms)":         p["aks_node_b2ms"],
            "PostgreSQL Flexible (B_Gen5)":   p["postgresql_flex_b"],
            f"ADLS Gen2 ({storage_gb} GB)":           round(storage_gb * p["adls_gb"], 2),
            "ADLS Operations (100k)":         round(_DEFAULT_REQUESTS_K * p["adls_ops_k"], 2),
            "ACR Basic":                      p["acr_basic"],
            "Data Transfer (10 GB out)":      round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
        }
    elif cloud == "gcp":
        items = {
            "GKE Control Plane":                  p["gke_control_plane"],
            "GKE Node (n2-standard-2, preempt)":  p["gke_node_n2_std2"],
            "Cloud SQL (db-g1-small)":             p["cloud_sql_g1"],
            f"GCS Storage ({storage_gb} GB)":              round(storage_gb * p["gcs_gb"], 2),
            "GCS Operations (100k)":               round(_DEFAULT_REQUESTS_K * p["gcs_ops_k"], 2),
            f"Artifact Registry ({_DEFAULT_IMAGE_GB} GB)": round(_DEFAULT_IMAGE_GB * p["artifact_reg_gb"], 2),
            "Data Transfer (10 GB out)":           round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
        }
    else:  # databricks
        jobs_mo  = round(p["jobs_dbu_count"] * p["jobs_dbu_hr"] * p["jobs_hours_day"] * _DAYS_PER_MONTH, 2)
        sql_mo   = round(p["sql_dbu_count"]  * p["sql_dbu_hr"]  * p["sql_hours_day"]  * _DAYS_PER_MONTH, 2)
        items = {
            f"Jobs Compute ({int(p['jobs_dbu_count'])} DBU × {int(p['jobs_hours_day'])}h/day)": jobs_mo,
            f"SQL Warehouse XS ({int(p['sql_dbu_count'])} DBU × {int(p['sql_hours_day'])}h/day)": sql_mo,
            f"Delta Lake / ADLS ({storage_gb} GB)": round(storage_gb * p["delta_storage_gb"], 2),
            "Unity Catalog":                         p["unity_catalog"],
            "Data Transfer (10 GB out)":             round(_DEFAULT_TRANSFER_GB * p["data_transfer_gb"], 2),
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
