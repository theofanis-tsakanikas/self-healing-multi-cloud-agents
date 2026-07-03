"""Eval mode — score the CURRENT LLM's diagnosis quality on the corpus (needs an LLM key).

The offline replay proves the *deterministic* routing + gate never regress. This measures
the part the replay can't: does the actual model, given a CI log, **call `request_fix` with
the right target and gate-valid verbatim evidence** — or does it stall / hallucinate / quote
nothing? That is the model-regression signal the whack-a-mole guards never had. It is
model-agnostic: point it at any provider via `LLM_MODEL` / `LLM_PROVIDER` (the same
`get_llm` seam the agent uses).

    LLM_MODEL=gpt-4o        OPENAI_API_KEY=...    python -m evals.harness.eval_live
    LLM_MODEL=claude-...    ANTHROPIC_API_KEY=... python -m evals.harness.eval_live --model claude-3-5-sonnet-latest

It invokes the model with a faithful, self-contained Medic instruction and the REAL
`request_fix` tool + gate (`agents.tools`), so the evidence check is the shipping one. It
deliberately does NOT spin the whole `medic_node`/graph — the point is to isolate the LLM's
judgment, not re-test the deterministic scaffolding the replay already covers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.harness.deterministic import evidence_gate_accepts, load_corpus, resolve_owner  # noqa: E402

_MEDIC_INSTRUCTION = """You are the Medic in a self-healing multi-cloud data-pipeline agent. A CI run has FAILED.
Below is the exact CI log. Diagnose the root cause and call the request_fix tool EXACTLY ONCE.

Routing (target_agent):
- 'architect' — a SCRIPT-LOGIC bug in the pipeline code (KeyError / ValueError / TypeError /
  NameError / AttributeError / AnalysisException / 'cannot resolve', or a Trino SQL / DDL-type error).
- 'infra' — a missing PROVISIONED RESOURCE: a JVM class / JDBC driver library not attached
  (ClassNotFoundException), a Databricks secret or scope that 'does not exist' /
  RESOURCE_DOES_NOT_EXIST, or any other Databricks provisioning/permission failure.

evidence_quote: copy the exact failing line(s) VERBATIM from the log. It MUST contain a real
error marker (e.g. 'Error:', 'Exception', 'Traceback', 'FAILED', 'does not exist'). Never
paraphrase and never invent an error. If the log shows no real failure, do NOT call request_fix.

CI LOG:
------
{log}
------
"""


def _extract_request_fix_call(ai_message) -> dict | None:
    for tc in getattr(ai_message, "tool_calls", None) or []:
        if tc.get("name") == "request_fix":
            return tc.get("args", {}) or {}
    return None


def diagnose_once(log_text: str, model: str | None = None) -> dict:
    """Run one live-LLM diagnosis; return the model's request_fix call + whether its evidence
    passes the REAL gate. Raises if no LLM key/model is configured."""
    from agents.llm_factory import get_llm
    from agents.tools import request_fix

    if model:
        os.environ["LLM_MODEL"] = model
    llm = get_llm(temperature=0)
    llm_with_tools = llm.bind_tools([request_fix])
    ai = llm_with_tools.invoke(_MEDIC_INSTRUCTION.format(log=log_text))

    call = _extract_request_fix_call(ai)
    if not call:
        return {"request_fix_called": False}
    evidence = call.get("evidence_quote", "")
    return {
        "request_fix_called": True,
        "target_agent": call.get("target_agent", ""),
        "issue_description": call.get("issue_description", ""),
        "suggested_fix": call.get("suggested_fix", ""),
        "evidence_quote": evidence,
        "evidence_passes_gate": evidence_gate_accepts(evidence) if evidence else False,
    }


def run_eval(cases: list[dict], model: str | None = None) -> dict:
    """Score every corpus case with the live model. For failure cases: did it call
    request_fix, route to the deterministic owner, and quote gate-valid evidence? For
    negative cases (clean/green/speculation): did it correctly NOT call request_fix?"""
    rows = []
    called_ok = target_ok = evidence_ok = negative_ok = 0
    n_failures = n_negatives = 0
    for c in cases:
        is_negative = c.get("expected_owner") is None
        d = diagnose_once(c["trigger_log"], model=model)
        row = {"id": c["id"], "failure_class": c["failure_class"], **d}
        if is_negative:
            n_negatives += 1
            row["correct"] = not d["request_fix_called"]
            negative_ok += row["correct"]
        else:
            n_failures += 1
            called = d["request_fix_called"]
            called_ok += called
            expected_owner = resolve_owner(c["trigger_log"]) or c["expected_owner"]
            row["expected_owner"] = expected_owner
            row["target_ok"] = called and d.get("target_agent") == expected_owner
            row["evidence_ok"] = called and d.get("evidence_passes_gate", False)
            target_ok += row["target_ok"]
            evidence_ok += row["evidence_ok"]
        rows.append(row)

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else 100.0

    return {
        "model": os.getenv("LLM_MODEL", "gpt-4o"),
        "failure_cases": n_failures,
        "called_request_fix_pct": pct(called_ok, n_failures),
        "correct_target_pct": pct(target_ok, n_failures),
        "valid_evidence_pct": pct(evidence_ok, n_failures),
        "negative_cases": n_negatives,
        "correctly_refused_pct": pct(negative_ok, n_negatives),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live-LLM eval of the Medic's diagnosis quality (needs an LLM key).")
    parser.add_argument("--model", help="override LLM_MODEL (e.g. gpt-4o-mini, claude-3-5-sonnet-latest)")
    parser.add_argument("--json", action="store_true", help="print the full per-case JSON")
    args = parser.parse_args(argv)

    result = run_eval(load_corpus(), model=args.model)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"model: {result['model']}  ({result['failure_cases']} failure + {result['negative_cases']} negative cases)")
        print(f"  called request_fix : {result['called_request_fix_pct']}%")
        print(f"  correct target     : {result['correct_target_pct']}%")
        print(f"  valid evidence     : {result['valid_evidence_pct']}%")
        print(f"  correctly refused  : {result['correctly_refused_pct']}%  (clean/green/speculation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
