"""
Business Rules Loader.

Supports three sources:
  1. Demo rules  — our pre-built YAML files in configs/business_rules/
  2. File upload — customer uploads YAML or JSON (two accepted formats)
  3. NL extract  — GPT extracts rules from a plain-English description

All sources normalise to the same internal dict structure (rules_conf)
so the rest of the pipeline needs zero changes.

── Internal format (same as configs/business_rules/*.yaml) ──────────────────
domain: "sales"
domain_description: "..."
quality_standards:
  - capability: "mandatory_fields"
    description: "..."
    target_criteria: "order_id column"
    logic: "Reject rows where order_id IS NULL"
    on_failure_action: "REJECT_ROW"   # ABORT_PIPELINE | REJECT_ROW | NULLIFY_FIELD | COERCE | KEEP_MOST_RECENT | MARK_AS_INCOMPLETE

── Simple customer format (auto-converted) ──────────────────────────────────
domain: "my_sales"
rules:
  - name: "No null order IDs"
    check: not_null        # not_null | range | pii_mask | regex | unique | freshness
    columns: [order_id]    # for column-list checks
    column: amount         # for single-column checks
    min: 0                 # for range
    max: 1000000
    pattern: "^\\d+$"      # for regex
    max_hours: 48          # for freshness
    action: REJECT_ROW     # optional override, default per check type
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import yaml

from utils.llm_defaults import NL_MODEL

_RULES_DIR = Path(__file__).resolve().parent.parent / "configs" / "business_rules"

# Default on_failure_action per check type
_DEFAULT_ACTION = {
    "not_null":  "REJECT_ROW",
    "range":     "REJECT_ROW",
    "pii_mask":  "ABORT_PIPELINE",
    "regex":     "NULLIFY_FIELD",
    "unique":    "KEEP_MOST_RECENT",
    "freshness": "ABORT_PIPELINE",
}

# ── 1. Demo rules ─────────────────────────────────────────────────────────────

def load_demo_rules(domain: str = "sales") -> dict:
    """Load one of our pre-built rule files. domain: sales | crm | marketing"""
    candidates = [
        _RULES_DIR / f"{domain}_logic.yaml",
        _RULES_DIR / f"{domain}.yaml",
    ]
    for path in candidates:
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f)
    # last resort: first file found
    files = sorted(_RULES_DIR.glob("*.yaml"))
    if files:
        with open(files[0]) as f:
            return yaml.safe_load(f)
    return {"domain": domain, "quality_standards": []}


def list_demo_domains() -> list[str]:
    return [p.stem.replace("_logic", "") for p in sorted(_RULES_DIR.glob("*.yaml"))]


# ── 2. File upload ────────────────────────────────────────────────────────────

def parse_rules_file(content: bytes, filename: str) -> dict:
    """
    Parse an uploaded YAML or JSON rules file.
    Accepts both the internal format and the simple customer format.
    Raises ValueError with a human-readable message on bad input.
    """
    try:
        if filename.lower().endswith(".json"):
            raw = json.loads(content.decode("utf-8"))
        else:
            raw = yaml.safe_load(content.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Could not parse file: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("File must be a YAML/JSON object (dict), not a list.")

    # Already in internal format?
    if "quality_standards" in raw:
        _validate_internal(raw)
        return raw

    # Simple customer format — convert
    if "rules" in raw:
        return _convert_simple(raw)

    raise ValueError(
        "Unrecognised format. File must contain either 'quality_standards' "
        "(internal format) or 'rules' (simple format). "
        "Download the template below for reference."
    )


def _validate_internal(rules: dict):
    required = {"capability", "logic", "on_failure_action"}
    for i, s in enumerate(rules.get("quality_standards", [])):
        missing = required - set(s.keys())
        if missing:
            raise ValueError(f"quality_standards[{i}] missing keys: {missing}")


def _convert_simple(raw: dict) -> dict:
    standards = []
    for r in raw.get("rules", []):
        check  = r.get("check", "not_null")
        action = r.get("action", _DEFAULT_ACTION.get(check, "REJECT_ROW")).upper()
        name   = r.get("name", check)

        if check == "not_null":
            cols  = r.get("columns", r.get("column", []))
            logic = f"Reject rows where any of {cols} IS NULL."
            criteria = f"Columns: {cols}"
        elif check == "range":
            col   = r.get("column", "value")
            lo, hi = r.get("min", ""), r.get("max", "")
            logic = f"Column '{col}' must be in range [{lo}, {hi}]."
            criteria = f"Column: {col}"
        elif check == "pii_mask":
            cols  = r.get("columns", [])
            logic = f"Mask/hash PII in columns: {cols}."
            criteria = f"Columns: {cols}"
        elif check == "regex":
            col     = r.get("column", "")
            pattern = r.get("pattern", "")
            logic   = f"Column '{col}' must match pattern: {pattern}"
            criteria = f"Column: {col}"
        elif check == "unique":
            cols  = r.get("columns", r.get("column", []))
            logic = f"Values in {cols} must be unique. Deduplicate on conflict."
            criteria = f"Columns: {cols}"
        elif check == "freshness":
            max_h = r.get("max_hours", 24)
            logic = f"Data must not be older than {max_h} hours."
            criteria = "Ingestion timestamp"
        else:
            logic    = r.get("logic", str(r))
            criteria = r.get("target_criteria", "")

        standards.append({
            "capability":       name,
            "description":      r.get("description", name),
            "target_criteria":  criteria,
            "logic":            logic,
            "on_failure_action": action,
        })

    return {
        "domain":              raw.get("domain", "custom"),
        "domain_description":  raw.get("description", "Customer-supplied rules"),
        "quality_standards":   standards,
    }


# ── 3. NL extraction ──────────────────────────────────────────────────────────

_NL_SYSTEM = """\
You are a data quality engineer. Extract ALL business rules and data quality
requirements from the user's pipeline description and return them as JSON.

Return this exact schema — no markdown, no commentary:
{
  "domain": "<inferred domain name, e.g. sales>",
  "domain_description": "<one sentence>",
  "quality_standards": [
    {
      "capability": "<short rule name>",
      "description": "<what this rule enforces>",
      "target_criteria": "<which column(s) or condition>",
      "logic": "<exact check to perform>",
      "on_failure_action": "<ABORT_PIPELINE|REJECT_ROW|NULLIFY_FIELD|COERCE|KEEP_MOST_RECENT|MARK_AS_INCOMPLETE>"
    }
  ]
}

If no explicit rules are mentioned, infer sensible defaults for the domain.
Always include at least: null checks on key columns, PII masking if personal
data is mentioned, and a freshness check.
"""


def extract_rules_from_nl(description: str) -> dict:
    """Use GPT-4o-mini to extract rules from a plain-English description."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return _fallback_rules(description)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=NL_MODEL,
            messages=[
                {"role": "system", "content": _NL_SYSTEM},
                {"role": "user",   "content": description},
            ],
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        raw = json.loads(resp.choices[0].message.content)
        _validate_internal(raw)
        return raw
    except Exception:
        return _fallback_rules(description)


def _fallback_rules(description: str) -> dict:
    desc_lower = description.lower()
    standards = [
        {
            "capability":      "mandatory_key_fields",
            "description":     "Core identifiers must always be present.",
            "target_criteria": "Primary key columns (id, order_id, customer_id…)",
            "logic":           "Reject rows where primary key IS NULL.",
            "on_failure_action": "REJECT_ROW",
        },
        {
            "capability":      "data_freshness",
            "description":     "Pipeline must process recent data only.",
            "target_criteria": "Ingestion timestamp",
            "logic":           "Reject batches older than 48 hours.",
            "on_failure_action": "ABORT_PIPELINE",
        },
    ]
    if any(w in desc_lower for w in ["email", "pii", "personal", "gdpr", "phone", "name"]):
        standards.append({
            "capability":      "pii_protection",
            "description":     "Personal data must be masked before landing in the data lake.",
            "target_criteria": "Columns containing email, phone, name",
            "logic":           "Hash names (SHA-256); mask emails (a***@domain.com).",
            "on_failure_action": "ABORT_PIPELINE",
        })
    return {
        "domain":             "custom",
        "domain_description": "Rules inferred from description (no OpenAI key available).",
        "quality_standards":  standards,
    }


# ── Template download ─────────────────────────────────────────────────────────

SIMPLE_TEMPLATE = """\
# Business Rules — Simple Format
# Upload this file to define your pipeline's data quality rules.
# Supported checks: not_null | range | pii_mask | regex | unique | freshness

domain: "my_pipeline"
description: "My custom data quality rules"

rules:
  - name: "No null order IDs"
    check: not_null
    columns: [order_id, customer_id]

  - name: "Amount must be positive"
    check: range
    column: amount
    min: 0

  - name: "Mask customer emails"
    check: pii_mask
    columns: [email]

  - name: "No duplicate orders"
    check: unique
    columns: [order_id]

  - name: "Data freshness"
    check: freshness
    max_hours: 48
"""
