"""
Architecture Advisor — takes a plain-English business description and returns
a costed, opinionated 4-option comparison report (AWS / Azure / GCP / Databricks)
with a recommendation.
"""
from __future__ import annotations
import json
import os

from utils.cost_estimator import estimate_monthly_cost
from utils.llm_defaults import NL_MODEL

_SYSTEM = """\
You are an expert cloud data architect. A user will describe a data pipeline need
in plain English. You must return a JSON object with this exact schema:

{
  "summary": "<one sentence — what this pipeline does>",
  "specs": {
    "data_volume_gb_day": <number, estimate if not stated, default 50>,
    "frequency": "<hourly|daily|weekly|streaming>",
    "region": "<closest AWS region slug, e.g. eu-central-1>",
    "compliance": ["GDPR"|"HIPAA"|"PCI-DSS"|...],
    "source_type": "<PostgreSQL|MySQL|S3|API|Kafka|...>",
    "destination_type": "<data lake|data warehouse|real-time|...>",
    "needs_ml": <true|false>,
    "heavy_transforms": <true|false>
  },
  "clouds": {
    "aws": {
      "pros": ["<2-3 concrete pros for THIS workload>"],
      "cons": ["<1-2 concrete cons for THIS workload>"],
      "label": "<8 words max>"
    },
    "azure": { "pros": [...], "cons": [...], "label": "..." },
    "gcp":   { "pros": [...], "cons": [...], "label": "..." },
    "databricks": {
      "pros": ["<2-3 concrete pros — focus on Delta Lake, Spark, Unity Catalog, ML>"],
      "cons": ["<1-2 cons — cost at small scale, learning curve>"],
      "label": "<8 words max>",
      "host_cloud": "<aws|azure|gcp — which cloud to run Databricks on>",
      "recommended_when": "<one sentence: when this workload justifies Databricks>"
    }
  },
  "recommendation": "<aws|azure|gcp|databricks>",
  "recommendation_reason": "<2 sentences: why this option wins for this specific workload>"
}

Rules:
- Recommend Databricks when: data_volume > 200 GB/day, OR needs_ml=true,
  OR heavy_transforms=true, OR destination is data warehouse/lakehouse
- Recommend Azure for GDPR/EU compliance at moderate scale
- pros/cons must be specific to the described workload, not generic copy
- Return ONLY the JSON object, no markdown, no commentary
"""


def analyze(description: str) -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key:
        try:
            raw    = _call_gpt(description, api_key)
            parsed = json.loads(raw)
            return _build_report(parsed)
        except Exception:
            pass
    return _heuristic_report(description)


def _call_gpt(description: str, api_key: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=NL_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": description},
        ],
        temperature=0.2,
        max_tokens=1100,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def _build_report(parsed: dict) -> dict:
    specs    = parsed.get("specs", {})
    vol_gb   = int(specs.get("data_volume_gb_day", 50))
    clouds_a = parsed.get("clouds", {})

    options = []
    for cloud in ("aws", "azure", "gcp", "databricks"):
        est  = estimate_monthly_cost(cloud, storage_gb=vol_gb)
        meta = clouds_a.get(cloud, {})
        opt  = {
            "cloud": cloud,
            "total": est["total"],
            "items": est["items"],
            "pros":  meta.get("pros",  []),
            "cons":  meta.get("cons",  []),
            "label": meta.get("label", ""),
        }
        if cloud == "databricks":
            opt["host_cloud"]       = meta.get("host_cloud", "azure")
            opt["recommended_when"] = meta.get("recommended_when", "")
        options.append(opt)

    rec = parsed.get("recommendation", _cheapest(options))
    return {
        "summary":               parsed.get("summary", ""),
        "specs":                 specs,
        "options":               options,
        "recommendation":        rec,
        "recommendation_reason": parsed.get("recommendation_reason", ""),
    }


def _cheapest(options: list[dict]) -> str:
    return min(options, key=lambda o: o["total"])["cloud"]


def _heuristic_report(description: str) -> dict:
    desc_lower  = description.lower()
    vol_gb      = 50
    compliance  = ["GDPR"] if any(w in desc_lower for w in ["eu", "europe", "gdpr", "pii"]) else []
    needs_ml    = any(w in desc_lower for w in ["ml", "model", "predict", "train", "machine learning"])
    heavy_etl   = any(w in desc_lower for w in ["transform", "aggregate", "join", "spark", "warehouse", "lakehouse"])
    large_scale = any(w in desc_lower for w in ["tb", "terabyte", "billion", "large scale"])

    options = []
    for cloud in ("aws", "azure", "gcp", "databricks"):
        est = estimate_monthly_cost(cloud, storage_gb=vol_gb)
        options.append({
            "cloud":            cloud,
            "total":            est["total"],
            "items":            est["items"],
            "pros":             _GENERIC_PROS[cloud],
            "cons":             _GENERIC_CONS[cloud],
            "label":            _GENERIC_LABELS[cloud],
            **( {"host_cloud": "azure",
                 "recommended_when": "When you need Delta Lake, Spark at scale, or ML pipelines."}
                if cloud == "databricks" else {} ),
        })

    if needs_ml or heavy_etl or large_scale:
        rec    = "databricks"
        reason = ("Databricks is the right choice: Delta Lake gives you ACID transactions and "
                  "schema evolution out of the box, and Unity Catalog handles governance across clouds. "
                  "For ML or heavy transformations, Spark on Databricks outperforms plain Kubernetes.")
    elif compliance:
        rec    = "azure"
        reason = ("Azure is recommended: free AKS control plane keeps costs lowest, "
                  "and native EU data-residency guarantees simplify GDPR compliance.")
    else:
        rec    = _cheapest(options)
        reason = f"{rec.upper()} offers the best price-performance for this workload."

    return {
        "summary":               description[:100],
        "specs":                 {"data_volume_gb_day": vol_gb, "frequency": "daily",
                                  "region": "eu-central-1", "compliance": compliance,
                                  "source_type": "PostgreSQL", "destination_type": "data lake",
                                  "needs_ml": needs_ml, "heavy_transforms": heavy_etl},
        "options":               options,
        "recommendation":        rec,
        "recommendation_reason": reason,
    }


_GENERIC_PROS = {
    "aws":        ["Most mature ecosystem", "Best-in-class S3 tooling", "Widest service catalogue"],
    "azure":      ["Free AKS control plane", "Native GDPR/EU compliance", "Lowest cost for moderate scale"],
    "gcp":        ["Best Trino/BigQuery integration", "Preemptible nodes reduce cost", "Fastest networking"],
    "databricks": ["Delta Lake: ACID + time travel + schema evolution",
                   "Unity Catalog: governance across all clouds",
                   "ML-ready: MLflow + Mosaic AI built-in"],
}
_GENERIC_CONS = {
    "aws":        ["Highest list price", "IAM complexity"],
    "azure":      ["Steeper initial setup", "ADLS URI syntax unfamiliar"],
    "gcp":        ["GKE control plane fee", "Smaller partner ecosystem"],
    "databricks": ["Higher cost at small scale vs plain K8s", "Vendor lock-in on Delta format"],
}
_GENERIC_LABELS = {
    "aws":        "Industry standard, richest ecosystem",
    "azure":      "Cheapest at moderate scale, GDPR-native",
    "gcp":        "Best analytics & ML performance",
    "databricks": "Lakehouse platform — Delta Lake + Spark + ML",
}
