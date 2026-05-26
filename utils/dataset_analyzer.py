"""
Dataset Analyzer.

Accepts an uploaded file (CSV / Parquet / JSON) and returns:
  - schema        : column names + inferred types
  - stats         : row count, size_mb, null rates, unique counts
  - pii_fields    : columns that look like personal data
  - quality_issues: detected problems (high null rate, duplicates, etc.)
  - size_gb_day   : estimated daily volume (used by cost estimator)
  - suggested_rules: ready-to-use rules_conf based on findings
"""
from __future__ import annotations
import io
import re
from pathlib import Path

import pandas as pd

# ── PII detection patterns ────────────────────────────────────────────────────

_PII_NAME_PATTERNS = re.compile(
    r"(email|e_mail|phone|mobile|tel|address|addr|postcode|zip|"
    r"first_?name|last_?name|full_?name|surname|passport|ssn|"
    r"national_?id|dob|birth|gender|ip_?addr)",
    re.IGNORECASE,
)

_EMAIL_PATTERN  = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_PATTERN  = re.compile(r"^\+?[\d\s\-().]{7,20}$")


def _looks_like_email(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).head(20)
    return sample.str.match(_EMAIL_PATTERN).mean() > 0.6


def _looks_like_phone(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).head(20)
    return sample.str.match(_PHONE_PATTERN).mean() > 0.6


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze(content: bytes, filename: str) -> dict:
    """
    Analyze an uploaded dataset file.
    Returns a dict with schema, stats, pii_fields, quality_issues,
    suggested_rules, and size_gb_day.
    """
    df = _load(content, filename)
    size_bytes = len(content)

    schema         = _build_schema(df)
    stats          = _build_stats(df, size_bytes)
    pii_fields     = _detect_pii(df)
    quality_issues = _detect_quality_issues(df)
    suggested_rules = _build_suggested_rules(df, pii_fields, quality_issues)

    # Estimate daily volume: assume file represents one day of data
    size_gb_day = max(round(size_bytes / 1024 / 1024 / 1024, 3), 0.001)

    return {
        "filename":        filename,
        "schema":          schema,
        "stats":           stats,
        "pii_fields":      pii_fields,
        "quality_issues":  quality_issues,
        "suggested_rules": suggested_rules,
        "size_gb_day":     size_gb_day,
        "dataframe":       df,
    }


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load(content: bytes, filename: str) -> pd.DataFrame:
    fn = filename.lower()
    if fn.endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    if fn.endswith(".json") or fn.endswith(".jsonl"):
        return pd.read_json(io.BytesIO(content), lines=fn.endswith(".jsonl"))
    if fn.endswith(".parquet"):
        return pd.read_parquet(io.BytesIO(content))
    raise ValueError(f"Unsupported file type: {Path(filename).suffix}. Use CSV, JSON, or Parquet.")


# ── Schema ────────────────────────────────────────────────────────────────────

_TYPE_MAP = {
    "int64": "integer", "int32": "integer",
    "float64": "float",  "float32": "float",
    "object": "string",  "bool": "boolean",
    "datetime64[ns]": "timestamp",
}

def _build_schema(df: pd.DataFrame) -> list[dict]:
    schema = []
    for col in df.columns:
        dtype = str(df[col].dtype)
        schema.append({
            "column":   col,
            "type":     _TYPE_MAP.get(dtype, dtype),
            "nullable": bool(df[col].isna().any()),
            "sample":   _safe_sample(df[col]),
        })
    return schema


def _safe_sample(series: pd.Series, n: int = 3) -> list:
    vals = series.dropna().head(n).tolist()
    return [str(v)[:40] for v in vals]


# ── Stats ─────────────────────────────────────────────────────────────────────

def _build_stats(df: pd.DataFrame, size_bytes: int) -> dict:
    null_rates = (df.isna().sum() / len(df) * 100).round(1).to_dict()
    return {
        "rows":        len(df),
        "columns":     len(df.columns),
        "size_mb":     round(size_bytes / 1024 / 1024, 2),
        "null_rates":  null_rates,
        "dup_rows":    int(df.duplicated().sum()),
    }


# ── PII detection ─────────────────────────────────────────────────────────────

def _detect_pii(df: pd.DataFrame) -> list[str]:
    pii = set()
    for col in df.columns:
        if _PII_NAME_PATTERNS.search(col):
            pii.add(col)
            continue
        if df[col].dtype == object:
            if _looks_like_email(df[col]):
                pii.add(col)
            elif _looks_like_phone(df[col]):
                pii.add(col)
    return sorted(pii)


# ── Quality issues ────────────────────────────────────────────────────────────

def _detect_quality_issues(df: pd.DataFrame) -> list[dict]:
    issues = []
    for col in df.columns:
        null_pct = df[col].isna().mean() * 100
        if null_pct > 20:
            issues.append({
                "column":   col,
                "issue":    "high_null_rate",
                "detail":   f"{null_pct:.1f}% null values",
                "severity": "high" if null_pct > 50 else "medium",
            })
    dup_count = df.duplicated().sum()
    if dup_count > 0:
        issues.append({
            "column":   "all",
            "issue":    "duplicate_rows",
            "detail":   f"{dup_count} duplicate rows ({dup_count/len(df)*100:.1f}%)",
            "severity": "medium",
        })
    return issues


# ── Suggested rules ───────────────────────────────────────────────────────────

def _build_suggested_rules(
    df: pd.DataFrame,
    pii_fields: list[str],
    quality_issues: list[dict],
) -> dict:
    standards = []

    # Not-null rule for low-null columns (likely key fields)
    key_cols = [
        col for col in df.columns
        if df[col].isna().mean() < 0.01
        and df[col].dtype in ("int64", "int32", "object")
    ][:5]
    if key_cols:
        standards.append({
            "capability":      "mandatory_fields",
            "description":     "Key columns must never be null.",
            "target_criteria": f"Columns: {key_cols}",
            "logic":           f"Reject rows where any of {key_cols} IS NULL.",
            "on_failure_action": "REJECT_ROW",
        })

    # PII masking
    if pii_fields:
        standards.append({
            "capability":      "pii_protection",
            "description":     "Personal data detected — must be masked before landing in data lake.",
            "target_criteria": f"Columns: {pii_fields}",
            "logic":           "Hash name fields (SHA-256); mask email fields (a***@domain.com).",
            "on_failure_action": "ABORT_PIPELINE",
        })

    # Duplicate check
    if any(i["issue"] == "duplicate_rows" for i in quality_issues):
        standards.append({
            "capability":      "deduplication",
            "description":     "Duplicate rows detected in source data.",
            "target_criteria": "All columns",
            "logic":           "Deduplicate on ingest — keep most recent record.",
            "on_failure_action": "KEEP_MOST_RECENT",
        })

    # High null columns → coerce
    high_null = [i["column"] for i in quality_issues if i["issue"] == "high_null_rate"]
    if high_null:
        standards.append({
            "capability":      "null_handling",
            "description":     f"Columns with high null rate: {high_null}",
            "target_criteria": f"Columns: {high_null}",
            "logic":           "Flag rows but do not reject — mark as INCOMPLETE.",
            "on_failure_action": "MARK_AS_INCOMPLETE",
        })

    # Freshness (always)
    standards.append({
        "capability":      "data_freshness",
        "description":     "Ensure pipeline is processing recent data.",
        "target_criteria": "Ingestion timestamp",
        "logic":           "Abort if batch is older than 48 hours.",
        "on_failure_action": "ABORT_PIPELINE",
    })

    return {
        "domain":             "auto_detected",
        "domain_description": "Rules auto-generated from dataset analysis.",
        "quality_standards":  standards,
    }
