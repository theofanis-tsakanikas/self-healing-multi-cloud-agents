"""The deterministic core of the harness — a faithful composition of the REAL Medic logic.

Nothing here re-implements the routing or the evidence gate; it imports and calls the
production functions (`agents.medic`, `agents.tools`) exactly as `medic_node` does, so a
green corpus means the *shipping* logic is correct — and a corpus case that regresses when
someone edits a signature list is caught immediately. No LLM, no network, no credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the repo root importable when run as a plain script (pytest sets pythonpath=["."]).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from langchain_core.messages import HumanMessage  # noqa: E402

from agents.medic import _ci_error_owner, _extract_ci_failed_file, _owner_of_file  # noqa: E402
from agents.tools import _EVIDENCE_MARKERS, request_fix  # noqa: E402

CORPUS_PATH = _REPO_ROOT / "evals" / "corpus" / "corpus.json"


def _as_messages(trigger_log: str) -> list[HumanMessage]:
    """Wrap a CI log the way the Medic sees it — a message with `.content` (what the
    routing functions read via `getattr(m, "content", "")`)."""
    return [HumanMessage(content=trigger_log)]


def resolve_owner(trigger_log: str) -> str:
    """The Medic's deterministic CI-failure routing, composed in the production order:

    1. signature router `_ci_error_owner` → 'architect' | 'infra' | ''
    2. on '' → the failing `scripts/*.py` / `sql/*.sql` frame → `_owner_of_file`
    3. still '' → undetermined (the live Medic defers to the LLM's target_agent)
    """
    msgs = _as_messages(trigger_log)
    owner = _ci_error_owner(msgs)
    if owner:
        return owner
    failed_file = _extract_ci_failed_file(msgs)
    if failed_file:
        return _owner_of_file(failed_file)
    return ""


def evidence_gate_accepts(trigger_log: str) -> bool:
    """True iff the REAL `request_fix` tool accepts this text as genuine failure evidence
    (status REJECTED_BY_MEDIC). False = refused (TOOL_ERROR) because it holds no error
    marker — the anti-hallucination gate."""
    result = json.loads(
        request_fix.invoke(
            {
                "target_agent": "infra",
                "issue_description": "eval",
                "suggested_fix": "eval",
                "evidence_quote": trigger_log,
            }
        )
    )
    return result.get("status") == "REJECTED_BY_MEDIC"


def evidence_markers() -> tuple[str, ...]:
    """The production error-marker list the evidence gate keys on (for the report)."""
    return tuple(_EVIDENCE_MARKERS)


def load_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    """Load the failure corpus cases (skips the leading `_about` metadata)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["cases"]
