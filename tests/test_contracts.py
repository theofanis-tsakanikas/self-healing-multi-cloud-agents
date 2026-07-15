"""Pin the load-bearing string contracts to their producers.

If someone edits a producer's literal without updating agents/contracts.py (or vice-versa), this
fails — turning a silent, runtime-only hop break into a red hermetic suite.
"""
from pathlib import Path

import pytest

from agents import contracts

_ROOT = Path(__file__).resolve().parent.parent


def _src(*names: str) -> str:
    return "\n".join((_ROOT / n).read_text(encoding="utf-8") for n in names)


# (constant value, source files that must contain it verbatim)
_PRODUCERS = [
    (contracts.REJECTED_BY_MEDIC, ("agents/tools.py", "graph.py")),
    (contracts.PUSH_SUCCESS_PREFIX, ("agents/tools.py",)),
    (contracts.VALIDATION_FAILED, ("agents/tools.py",)),
    (contracts.AUTOVAL_FAILED_PREFIX, ("agents/infra.py",)),
    (contracts.STATE_LOCK_ERROR, ("agents/tools.py", "agents/medic.py")),
    (contracts.INFRA_COMPLETE, ("agents/infra.py",)),
    (contracts.RESOLVE_FROM_TF, ("agents/tools.py", "agents/codegen.py")),
]


@pytest.mark.parametrize("value,files", _PRODUCERS, ids=[p[0] for p in _PRODUCERS])
def test_contract_string_present_in_producer(value, files):
    for f in files:
        assert value in _src(f), f"contract {value!r} not found in {f} — producer drifted from agents/contracts.py"


@pytest.mark.parametrize("marker", contracts.CI_GREEN_MARKERS)
def test_ci_green_markers_present(marker):
    assert marker in _src("agents/tools.py"), f"CI green marker {marker!r} missing from fetch_github_action_logs"


def test_mission_status_values_match_state_contract():
    # The terminal contract lives in agents/state.py; these constants must mirror it.
    state_src = _src("agents/state.py")
    for value in (contracts.MISSION_VERIFIED, contracts.MISSION_ESCALATED, contracts.MISSION_CI_UNVERIFIED):
        assert value in state_src, f"mission_status value {value!r} not documented in agents/state.py"
