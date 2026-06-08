"""
Deterministic cloud cost model.

Estimates the **monthly USD cost of the fixed infrastructure footprint** this system
provisions per platform (AWS / Azure / GCP / Databricks). It is NOT a live pricing API and NOT
an LLM guess — it enumerates the exact resources each bootstrap creates and multiplies them by
published on-demand rates kept here as documented constants. Because the footprint is *known and
fixed*, the estimate is explainable line-by-line and reproducible.

Honesty / assumptions (surface these in any UI that shows the numbers):
  - On-demand pricing, representative region per cloud, **as of ~2026-06** (RATES below). Real
    bills vary by region/commitment/egress/data-scan, which are intentionally excluded.
  - "always_on" = the steady-state monthly bill if the stack runs 24/7 (the realistic number for
    the K8s clouds until you tear them down).
  - "demo" = what the showcase actually costs: the Databricks jobs cluster auto-terminates and the
    SQL warehouse is serverless (pay-per-use), so its real monthly cost is far below always-on and
    is dominated by the optional always-on source RDS.

Pure functions only — no I/O, no network. Safe to import anywhere (Streamlit, the NL recommender).
Used by nothing in the agent generation path, so it cannot affect the validated 4-cloud pipelines.
"""
from __future__ import annotations

HOURS_PER_MONTH = 730  # 365 * 24 / 12, the standard cloud-billing month

# ---------------------------------------------------------------------------
# Published on-demand rates (USD). Documented constants — update periodically.
# Each comment gives the unit; monthly = hourly * HOURS_PER_MONTH unless noted.
# ---------------------------------------------------------------------------
RATES = {
    # ── AWS (eu-central-1) ────────────────────────────────────────────────
    "aws_eks_control_plane_hr": 0.10,      # EKS control plane, per cluster-hour
    "aws_ec2_m5_large_hr": 0.115,          # m5.large on-demand (general-purpose node)
    "aws_eks_automode_surcharge": 0.12,    # EKS Auto Mode management fee ≈ 12% on top of EC2
    "aws_rds_t4g_micro_hr": 0.018,         # RDS db.t4g.micro (Postgres) single-AZ
    "aws_ebs_gp3_gb_mo": 0.0952,           # gp3 storage per GB-month (RDS allocated_storage)
    "aws_s3_gb_mo": 0.0245,                # S3 Standard per GB-month
    "aws_nat_gateway_hr": 0.052,           # NAT Gateway per hour (eu-central-1)
    "aws_alb_hr": 0.027,                   # Application Load Balancer per hour
    # ── Azure (West Europe) ───────────────────────────────────────────────
    "azure_d2s_v6_hr": 0.111,              # Standard_D2s_v6 (2 vCPU/8 GB) on-demand
    "azure_pg_b1ms_hr": 0.0182,            # Postgres Flexible B_Standard_B1ms
    "azure_adls_gb_mo": 0.0184,            # ADLS Gen2 Hot per GB-month
    "azure_lb_standard_hr": 0.025,         # Standard Load Balancer per hour
    # ── GCP (europe-west3) ────────────────────────────────────────────────
    "gcp_gke_mgmt_hr": 0.10,               # GKE Standard cluster management fee per hour
    "gcp_e2_standard_2_hr": 0.0760,        # e2-standard-2 (2 vCPU/8 GB) on-demand
    "gcp_cloudsql_f1_micro_hr": 0.0150,    # Cloud SQL db-f1-micro
    "gcp_gcs_gb_mo": 0.0230,               # GCS Standard per GB-month (europe)
    "gcp_lb_forwarding_hr": 0.025,         # Forwarding rule per hour
    # ── Databricks (host cloud = AWS) ─────────────────────────────────────
    # Jobs Compute DBU rate + the underlying EC2. m5d.xlarge ≈ 1.0 DBU/hr; Jobs Compute ≈ $0.15/DBU.
    "dbx_jobs_dbu_hr": 0.15,               # Jobs Compute, USD per DBU
    "dbx_m5d_xlarge_dbu": 1.0,             # DBUs consumed per m5d.xlarge per hour
    "aws_ec2_m5d_xlarge_hr": 0.226,        # m5d.xlarge on-demand (driver + worker)
    "dbx_sql_serverless_dbu_hr": 0.70,     # Serverless SQL, USD per DBU
    "dbx_sql_2xsmall_dbu_hr": 4.0,         # 2X-Small serverless warehouse ≈ 4 DBU/hr
}


def _mo(hourly: float) -> float:
    return round(hourly * HOURS_PER_MONTH, 2)


def _li(name: str, detail: str, monthly_usd: float) -> dict:
    return {"name": name, "detail": detail, "monthly_usd": round(monthly_usd, 2)}


# ---------------------------------------------------------------------------
# Per-cloud estimators — each returns a structured breakdown.
# Footprint defaults mirror the bootstraps; override via kwargs for what-if analysis.
# ---------------------------------------------------------------------------

def _estimate_aws(nodes: int = 2, storage_gb: int = 5, rds_storage_gb: int = 20) -> dict:
    ec2 = nodes * RATES["aws_ec2_m5_large_hr"]
    items = [
        _li("EKS control plane", "$0.10/hr", _mo(RATES["aws_eks_control_plane_hr"])),
        _li(f"{nodes}× m5.large nodes (Auto Mode)",
            f"{nodes}×$0.115/hr +12% mgmt", _mo(ec2 * (1 + RATES["aws_eks_automode_surcharge"]))),
        _li("RDS db.t4g.micro", "Postgres, single-AZ", _mo(RATES["aws_rds_t4g_micro_hr"])),
        _li("RDS storage", f"{rds_storage_gb} GB gp3", rds_storage_gb * RATES["aws_ebs_gp3_gb_mo"]),
        _li("S3 storage", f"{storage_gb} GB Standard", storage_gb * RATES["aws_s3_gb_mo"]),
        _li("NAT Gateway", "$0.052/hr (+ data)", _mo(RATES["aws_nat_gateway_hr"])),
        _li("Load Balancer (Grafana)", "ALB $0.027/hr", _mo(RATES["aws_alb_hr"])),
    ]
    return _bundle("aws", "eu-central-1", items, always_on=_total(items), demo=_total(items),
                   notes=["K8s stack runs 24/7 until torn down (cleanup_k8s.yml / destroy.yml)."])


def _estimate_azure(nodes: int = 2, storage_gb: int = 5) -> dict:
    items = [
        _li("AKS control plane", "Free tier", 0.0),
        _li(f"{nodes}× Standard_D2s_v6 nodes", f"{nodes}×$0.111/hr", _mo(nodes * RATES["azure_d2s_v6_hr"])),
        _li("Postgres Flexible B1ms", "B_Standard_B1ms", _mo(RATES["azure_pg_b1ms_hr"])),
        _li("ADLS Gen2 storage", f"{storage_gb} GB Hot", storage_gb * RATES["azure_adls_gb_mo"]),
        _li("Load Balancer (Grafana)", "Standard $0.025/hr", _mo(RATES["azure_lb_standard_hr"])),
    ]
    return _bundle("azure", "westeurope", items, always_on=_total(items), demo=_total(items),
                   notes=["AKS control plane is free; nodes dominate. Runs 24/7 until torn down."])


def _estimate_gcp(nodes: int = 2, storage_gb: int = 5) -> dict:
    items = [
        _li("GKE management fee", "$0.10/hr (Standard)", _mo(RATES["gcp_gke_mgmt_hr"])),
        _li(f"{nodes}× e2-standard-2 nodes", f"{nodes}×$0.076/hr", _mo(nodes * RATES["gcp_e2_standard_2_hr"])),
        _li("Cloud SQL db-f1-micro", "MySQL", _mo(RATES["gcp_cloudsql_f1_micro_hr"])),
        _li("GCS storage", f"{storage_gb} GB Standard", storage_gb * RATES["gcp_gcs_gb_mo"]),
        _li("Load Balancer (Grafana)", "Forwarding rule $0.025/hr", _mo(RATES["gcp_lb_forwarding_hr"])),
    ]
    return _bundle("gcp", "europe-west3", items, always_on=_total(items), demo=_total(items),
                   notes=["One zonal GKE cluster/billing-account may be free — then deduct the mgmt fee."])


def _estimate_databricks(
    job_hours_per_month: float = 5.0,      # daily ~10-min run → ~5 hrs/mo of jobs compute
    warehouse_hours_per_month: float = 10.0,  # dashboard queries, serverless
    rds_always_on: bool = True,
    storage_gb: int = 5,
) -> dict:
    # Jobs cluster = driver + 1 worker (2× m5d.xlarge): DBUs + EC2, only while a job runs.
    dbu_per_hr = 2 * RATES["dbx_m5d_xlarge_dbu"]
    jobs_dbu_cost = job_hours_per_month * dbu_per_hr * RATES["dbx_jobs_dbu_hr"]
    jobs_ec2_cost = job_hours_per_month * 2 * RATES["aws_ec2_m5d_xlarge_hr"]
    sql_cost = warehouse_hours_per_month * RATES["dbx_sql_2xsmall_dbu_hr"] * RATES["dbx_sql_serverless_dbu_hr"]
    rds_mo = _mo(RATES["aws_rds_t4g_micro_hr"]) + 20 * RATES["aws_ebs_gp3_gb_mo"]

    items = [
        _li("Jobs cluster (DBU)", f"{job_hours_per_month}h × {dbu_per_hr} DBU × $0.15", jobs_dbu_cost),
        _li("Jobs cluster (EC2)", f"{job_hours_per_month}h × 2× m5d.xlarge", jobs_ec2_cost),
        _li("Serverless SQL warehouse", f"{warehouse_hours_per_month}h × 2X-Small", sql_cost),
        _li("Source RDS db.t4g.micro", "Postgres + 20 GB" + ("" if rds_always_on else " (stopped)"),
            rds_mo if rds_always_on else 0.0),
        _li("S3 (DBFS + managed)", f"{storage_gb} GB", storage_gb * RATES["aws_s3_gb_mo"]),
    ]
    demo = _total(items)
    # "always_on" hypothetical: if the jobs cluster never auto-terminated (1+1 m5d.xlarge 24/7).
    always_on_cluster = _mo(dbu_per_hr * RATES["dbx_jobs_dbu_hr"] + 2 * RATES["aws_ec2_m5d_xlarge_hr"])
    always_on = round(demo - jobs_dbu_cost - jobs_ec2_cost + always_on_cluster, 2)
    return _bundle(
        "databricks", "eu-central-1 (host AWS)", items, always_on=always_on, demo=demo,
        notes=[
            "Usage-based: the jobs cluster AUTO-TERMINATES and the SQL warehouse is SERVERLESS, so",
            "real cost ≈ the 'demo' figure (dominated by the optional always-on source RDS).",
            "'always_on' assumes the jobs cluster ran 24/7 — not how it actually runs.",
        ],
    )


# ---------------------------------------------------------------------------
# Shared helpers + public API
# ---------------------------------------------------------------------------

def _total(items: list[dict]) -> float:
    return round(sum(i["monthly_usd"] for i in items), 2)


def _bundle(cloud, region, items, always_on, demo, notes) -> dict:
    return {
        "cloud": cloud,
        "region": region,
        "currency": "USD",
        "line_items": items,
        "monthly_always_on_usd": round(always_on, 2),
        "monthly_demo_usd": round(demo, 2),
        "notes": notes,
        "disclaimer": "Estimate — on-demand pricing, ~2026-06, excludes egress/data-scan. Real bills vary.",
    }


_ESTIMATORS = {
    "aws": _estimate_aws,
    "azure": _estimate_azure,
    "gcp": _estimate_gcp,
    "databricks": _estimate_databricks,
}

SUPPORTED_CLOUDS = tuple(_ESTIMATORS.keys())


def estimate_monthly_cost(cloud: str, **overrides) -> dict:
    """Return the monthly cost breakdown for one cloud. `cloud` ∈ SUPPORTED_CLOUDS.
    `overrides` tune the footprint (e.g. nodes=3, storage_gb=50, job_hours_per_month=20)."""
    key = cloud.lower()
    if key not in _ESTIMATORS:
        raise ValueError(f"Unknown cloud '{cloud}'. Supported: {', '.join(SUPPORTED_CLOUDS)}")
    return _ESTIMATORS[key](**overrides)


def compare_all(**overrides) -> dict:
    """Estimate every supported cloud. Returns {cloud: breakdown}. Overrides are passed only to
    estimators that accept them (so a global storage_gb works across all four)."""
    out = {}
    for cloud, fn in _ESTIMATORS.items():
        accepted = fn.__code__.co_varnames[: fn.__code__.co_argcount]
        out[cloud] = fn(**{k: v for k, v in overrides.items() if k in accepted})
    return out


def cheapest(metric: str = "monthly_demo_usd", **overrides) -> str:
    """Return the cloud with the lowest cost on `metric` (monthly_demo_usd | monthly_always_on_usd)."""
    allc = compare_all(**overrides)
    return min(allc, key=lambda c: allc[c][metric])


def format_report(breakdown: dict) -> str:
    """Human-readable single-cloud report (CLI / logs)."""
    lines = [f"  {breakdown['cloud'].upper()}  ({breakdown['region']})"]
    for it in breakdown["line_items"]:
        lines.append(f"    {it['name']:<34} {it['detail']:<28} ${it['monthly_usd']:>8.2f}/mo")
    lines.append(f"    {'─' * 74}")
    lines.append(f"    {'TOTAL (always-on)':<34} {'':<28} ${breakdown['monthly_always_on_usd']:>8.2f}/mo")
    lines.append(f"    {'TOTAL (typical demo)':<34} {'':<28} ${breakdown['monthly_demo_usd']:>8.2f}/mo")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Monthly cost estimate — fixed footprint per platform "
          "(USD, on-demand, ~2026-06, excl. egress/data-scan)\n")
    allc = compare_all()
    for cloud in SUPPORTED_CLOUDS:
        print(format_report(allc[cloud]))
        print()
    print(f"  Cheapest (typical demo):     {cheapest('monthly_demo_usd').upper()}")
    print(f"  Cheapest (always-on 24/7):   {cheapest('monthly_always_on_usd').upper()}")
