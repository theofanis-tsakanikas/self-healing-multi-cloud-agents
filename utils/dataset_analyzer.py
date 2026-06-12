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

# ── Column-name hints for deterministic rule suggestions ──────────────────────
_NONNEG_NAME = re.compile(
    r"(amount|price|cost|qty|quantity|count|total|balance|salary|revenue|age)",
    re.IGNORECASE,
)
_DATE_NAME = re.compile(
    r"(date|time|timestamp|created|updated|dob|birth|_at$|_on$)",
    re.IGNORECASE,
)


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

def _rule(
    capability: str,
    description: str,
    target_criteria: str,
    logic: str,
    on_failure_action: str,
    confidence: str,
    severity: str,
) -> dict:
    """One suggested rule.

    confidence = how strongly the DATA supports this rule (high|medium|low);
    severity   = the impact if it is violated (high|medium|low).
    Both let the UI rank suggestions — confident ones surface first.
    """
    return {
        "capability":        capability,
        "description":       description,
        "target_criteria":   target_criteria,
        "logic":             logic,
        "on_failure_action": on_failure_action,
        "confidence":        confidence,
        "severity":          severity,
    }


_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def _build_suggested_rules(
    df: pd.DataFrame,
    pii_fields: list[str],
    quality_issues: list[dict],
) -> dict:
    """Deterministic data-quality profiler (a mini Great-Expectations).

    Every rule is derived from MEASURABLE properties of the dataframe — null
    rates, PII patterns, duplicates, ranges, cardinality, uniqueness, formats —
    so the closed set of conditions is owned by code, never an LLM. Domain /
    business rules (e.g. "discount must be <= 50%") need semantics the data does
    not carry and belong to the NL/LLM path, not a hardcoded catalog here.
    """
    standards: list[dict] = []
    n_rows = len(df)
    numeric_cols = list(df.select_dtypes(include="number").columns)
    object_cols = list(df.select_dtypes(include="object").columns)

    # 1. Mandatory fields — low-null key columns.
    key_cols = [
        col for col in df.columns
        if df[col].isna().mean() < 0.01
        and df[col].dtype in ("int64", "int32", "object")
    ][:5]
    if key_cols:
        standards.append(_rule(
            "mandatory_fields",
            "Key columns must never be null.",
            f"Columns: {key_cols}",
            f"Reject rows where any of {key_cols} IS NULL.",
            "REJECT_ROW", "high", "high",
        ))

    # 2. PII masking.
    if pii_fields:
        standards.append(_rule(
            "pii_protection",
            "Personal data detected — must be masked before landing in the data lake.",
            f"Columns: {pii_fields}",
            "Hash name fields (SHA-256); mask email fields (a***@domain.com).",
            "ABORT_PIPELINE", "high", "high",
        ))

    # 3. Deduplication.
    if any(i["issue"] == "duplicate_rows" for i in quality_issues):
        standards.append(_rule(
            "deduplication",
            "Duplicate rows detected in source data.",
            "All columns",
            "Deduplicate on ingest — keep most recent record.",
            "KEEP_MOST_RECENT", "high", "medium",
        ))

    # 4. High-null columns.
    high_null = [i["column"] for i in quality_issues if i["issue"] == "high_null_rate"]
    if high_null:
        worst = "high" if any(
            i["issue"] == "high_null_rate" and i["severity"] == "high" for i in quality_issues
        ) else "medium"
        standards.append(_rule(
            "null_handling",
            f"Columns with high null rate: {high_null}",
            f"Columns: {high_null}",
            "Flag rows but do not reject — mark as INCOMPLETE.",
            "MARK_AS_INCOMPLETE", "high", worst,
        ))

    # 5. Non-negative measures — numeric columns whose NAME implies >= 0 but that hold negatives.
    for col in numeric_cols:
        if _NONNEG_NAME.search(str(col)):
            col_min = df[col].min()
            if pd.notna(col_min) and col_min < 0:
                neg = int((df[col] < 0).sum())
                standards.append(_rule(
                    "value_range",
                    f"'{col}' looks like a non-negative measure but holds {neg} negative value(s).",
                    f"Column: {col}",
                    f"Reject rows where {col} < 0.",
                    "REJECT_ROW", "high", "high",
                ))

    # 6. Unique-key candidate — a low-null column that is unique across every row.
    for col in df.columns:
        if n_rows > 1 and df[col].isna().mean() < 0.01 and df[col].nunique(dropna=True) == n_rows:
            standards.append(_rule(
                "uniqueness",
                f"'{col}' is unique across all rows — likely a primary key.",
                f"Column: {col}",
                f"Enforce uniqueness on {col}; reject duplicate keys.",
                "REJECT_ROW", "high", "high",
            ))
            break  # one primary-key suggestion is enough

    # 7. Controlled vocabulary — low-cardinality categorical columns.
    cat_added = 0
    for col in object_cols:
        if cat_added >= 2 or col in pii_fields:
            continue
        nunique = df[col].nunique(dropna=True)
        if 1 < nunique <= 20 and n_rows >= 20 and (nunique / n_rows) < 0.05:
            observed = sorted(df[col].dropna().astype(str).unique().tolist())[:20]
            standards.append(_rule(
                "allowed_values",
                f"'{col}' has only {nunique} distinct values — a controlled vocabulary.",
                f"Column: {col}",
                f"Value of {col} must be one of: {observed}.",
                "REJECT_ROW", "medium", "medium",
            ))
            cat_added += 1

    # 8. Statistical outliers (1.5x IQR) on numeric measures.
    out_added = 0
    for col in numeric_cols:
        if out_added >= 2:
            break
        s = df[col].dropna()
        if len(s) >= 20:
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                n_out = int(((s < lo) | (s > hi)).sum())
                if 0 < (n_out / len(s)) < 0.10:
                    standards.append(_rule(
                        "value_range",
                        f"'{col}' has {n_out} statistical outlier(s) (1.5x IQR) — review extreme values.",
                        f"Column: {col}",
                        f"Flag rows where {col} is outside [{lo:.2f}, {hi:.2f}].",
                        "MARK_AS_INCOMPLETE", "medium", "low",
                    ))
                    out_added += 1

    # 9. Format consistency — leading/trailing whitespace.
    ws_added = 0
    for col in object_cols:
        if ws_added >= 2:
            break
        s = df[col].dropna().astype(str)
        if len(s) and (s != s.str.strip()).any():
            standards.append(_rule(
                "format_consistency",
                f"'{col}' contains values with leading/trailing whitespace.",
                f"Column: {col}",
                f"Trim whitespace on {col} before landing; flag rows that needed cleaning.",
                "MARK_AS_INCOMPLETE", "medium", "low",
            ))
            ws_added += 1

    # 10. Date parseability — date-named text columns that do not fully parse.
    for col in object_cols:
        if _DATE_NAME.search(str(col)):
            s = df[col].dropna().astype(str)
            if len(s) >= 10:
                try:
                    fail = pd.to_datetime(s, errors="coerce").isna().mean()
                except Exception:
                    fail = 0.0
                if 0 < fail < 0.5:
                    standards.append(_rule(
                        "type_format",
                        f"'{col}' looks like a date but {fail * 100:.0f}% of values don't parse.",
                        f"Column: {col}",
                        f"Reject rows where {col} is not a valid date.",
                        "REJECT_ROW", "medium", "medium",
                    ))
            break  # one date-format suggestion is enough

    # 11. Freshness — ONLY when a temporal column exists (else it is irrelevant noise).
    has_time_col = (
        any(_DATE_NAME.search(str(c)) for c in df.columns)
        or len(df.select_dtypes(include="datetime").columns) > 0
    )
    if has_time_col:
        standards.append(_rule(
            "data_freshness",
            "Ensure the pipeline is processing recent data.",
            "Ingestion timestamp",
            "Abort if the batch is older than 48 hours.",
            "ABORT_PIPELINE", "medium", "medium",
        ))

    # Most-confident suggestions first — the UI surfaces high-confidence rules on top.
    standards.sort(key=lambda r: _CONFIDENCE_RANK.get(r.get("confidence", "low"), 2))

    return {
        "domain":             "auto_detected",
        "domain_description": "Rules auto-generated from dataset analysis.",
        "quality_standards":  standards,
    }
