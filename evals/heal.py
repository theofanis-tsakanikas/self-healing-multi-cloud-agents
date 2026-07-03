"""heal — point it at ANY failing CI log and get the Medic's verdict, offline.

Decouples the self-healing *judgment* from the pipelines this agent generated: feed it a
failure log from any repo/run and it applies the REAL Medic routing + evidence gate (no
cloud, no LLM, no credentials) to answer "which agent owns this, and is it a genuine
failure?". `--diagnose` adds a live-LLM root-cause + suggested fix (needs an LLM key).

    heal --log run.log
    heal --text "KeyError: 'campaign'"
    cat run.log | heal            # stdin
    heal --log run.log --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.harness.deterministic import evidence_gate_accepts, resolve_owner  # noqa: E402


def verdict(log_text: str) -> dict:
    """The deterministic Medic verdict for a failure log."""
    owner = resolve_owner(log_text)
    return {
        "is_real_failure": evidence_gate_accepts(log_text),
        "routed_to": owner or "",
        "routed_to_label": owner or "undetermined (the live Medic defers to the LLM)",
    }


def _read_input(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.log:
        return Path(args.log).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route + validate any failing CI log through the real Medic logic.")
    parser.add_argument("--log", help="path to a failure log file")
    parser.add_argument("--text", help="failure text passed directly")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--diagnose", action="store_true", help="add a live-LLM diagnosis (needs an LLM key)")
    args = parser.parse_args(argv)

    log_text = _read_input(args)
    if not log_text.strip():
        parser.error("no input — pass --log FILE, --text STR, or pipe a log on stdin")

    result = verdict(log_text)

    if args.diagnose:
        try:
            from evals.harness.eval_live import diagnose_once

            result["diagnosis"] = diagnose_once(log_text)
        except Exception as exc:  # pragma: no cover - needs a live key
            result["diagnosis"] = {"error": f"live diagnosis unavailable: {exc}"}

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    real = "real failure ✓" if result["is_real_failure"] else "no error marker — not actionable ✗"
    print(f"evidence gate : {real}")
    print(f"routed to     : {result['routed_to_label']}")
    if "diagnosis" in result:
        d = result["diagnosis"]
        if "error" in d:
            print(f"diagnosis     : {d['error']}")
        else:
            print(f"diagnosis     : [{d.get('target_agent', '?')}] {d.get('issue_description', '')}")
            print(f"suggested fix : {d.get('suggested_fix', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
