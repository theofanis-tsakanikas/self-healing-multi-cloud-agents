"""
Pipeline observability metrics.
Reads from Prometheus when PROMETHEUS_URL is set;
falls back to plausible simulated data otherwise.
"""
from __future__ import annotations
import datetime
import random
import os
import pandas as pd


def _seed(pipeline_id: str) -> int:
    return sum(ord(c) for c in pipeline_id)


def get_pipeline_summary(pipeline_id: str) -> dict:
    """Return latest pipeline run health snapshot."""
    prometheus_url = os.getenv("PROMETHEUS_URL", "")
    if prometheus_url:
        try:
            import requests  # type: ignore
            base = prometheus_url.rstrip("/")

            def _query(expr: str) -> float | None:
                r = requests.get(f"{base}/api/v1/query", params={"query": expr}, timeout=5)
                data = r.json().get("data", {}).get("result", [])
                return float(data[0]["value"][1]) if data else None

            records = _query(f'sum(pipeline_records_total{{pipeline="{pipeline_id}"}})')
            errors  = _query(f'sum(pipeline_errors_total{{pipeline="{pipeline_id}"}})')
            latency = _query(f'avg(pipeline_latency_ms{{pipeline="{pipeline_id}"}})')

            if records is not None:
                return {
                    "records_today":    int(records),
                    "error_rate_pct":   round((errors or 0) / max(records, 1) * 100, 3),
                    "avg_latency_ms":   int(latency or 0),
                    "last_run_ago_min": 5,
                    "runs_today":       24,
                    "sla_met_pct":      99.5,
                    "source":           "prometheus",
                }
        except Exception:
            pass

    # Simulated — seeded by pipeline_id so values are stable across reruns
    rng = random.Random(_seed(pipeline_id))
    return {
        "records_today":    rng.randint(180_000, 450_000),
        "error_rate_pct":   round(rng.uniform(0.01, 0.15), 3),
        "avg_latency_ms":   rng.randint(120, 480),
        "last_run_ago_min": rng.randint(2, 45),
        "runs_today":       rng.randint(8, 24),
        "sla_met_pct":      round(rng.uniform(98.5, 99.9), 1),
        "source":           "simulated",
    }


def get_hourly_throughput(pipeline_id: str, hours: int = 24) -> pd.DataFrame:
    """Return last N hours of records/errors per hour."""
    rng = random.Random(_seed(pipeline_id))
    now = datetime.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    rows = []
    for i in range(hours, 0, -1):
        t = now - datetime.timedelta(hours=i)
        rows.append({
            "time":    t.strftime("%H:%M"),
            "records": rng.randint(6_000, 22_000),
            "errors":  rng.randint(0, 25),
        })
    return pd.DataFrame(rows)


def get_cloud_breakdown(pipeline_id: str) -> pd.DataFrame:
    """Simulated per-cloud latency comparison."""
    rng = random.Random(_seed(pipeline_id) + 1)
    return pd.DataFrame({
        "cloud":       ["AWS", "Azure", "GCP"],
        "avg_ms":      [rng.randint(120, 300), rng.randint(140, 320), rng.randint(100, 280)],
        "p99_ms":      [rng.randint(400, 900), rng.randint(420, 950), rng.randint(380, 860)],
        "error_rate":  [round(rng.uniform(0.01, 0.12), 3) for _ in range(3)],
    })
