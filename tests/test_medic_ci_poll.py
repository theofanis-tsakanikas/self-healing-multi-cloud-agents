"""
Regression: on a PENDING (in_progress/queued) CI run, the medic must RE-FETCH the run state
DETERMINISTICALLY in Python — never relying on the LLM to re-call fetch_github_action_logs.
gpt-4o-mini follows the prompt's "if PENDING, finish your turn" and SKIPS the re-fetch on a
re-poll turn, so the run reaches FINISH with mission_status unset → MissionFailedError on an
otherwise-successful deploy (observed run 27655394818, 2026-06-17). The deterministic CI poll
(restored) fetches in Python: pending → re-poll (next_step='medic'), LLM not invoked.
"""
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage

import agents.medic as medic_mod
from agents.medic import medic_node


def _pending_state():
    return {
        "task": "verify", "messages": [HumanMessage(content="infra completed")],
        "error_log": "", "project_id": "PIPE-X-20260617-0930", "config_path": "",
        "target_infra": "aws", "written_files": ["scripts/x.py"],
        "infra_provisioned": True, "infra_status": "completed",
        "architect_status": "completed", "schema_discovered": True,
        "github_done": True, "last_push_sha": "abc1234", "medic_fix_requested": False,
        "ci_poll_attempt": 0, "fix_attempt": 0, "last_fix_signature": "",
        "medic_fix_target": "", "mission_status": "",
    }


def _run_pending_medic():
    pending = "PENDING: Run 999 still in progress (status: in_progress). Retry later."
    fetch_mock = MagicMock()
    fetch_mock.name = "fetch_github_action_logs"
    fetch_mock.invoke.return_value = pending

    llm_with_tools = MagicMock()  # must NOT be invoked on a pending poll
    llm = MagicMock()
    llm.bind_tools.return_value = llm_with_tools

    with patch.object(medic_mod, "get_llm", return_value=llm), \
         patch.object(medic_mod, "fetch_github_action_logs", fetch_mock), \
         patch.object(medic_mod, "store_architectural_insight", MagicMock()), \
         patch.object(medic_mod.time, "sleep", MagicMock()):   # skip the real backoff wait
        out = medic_node(_pending_state())
    return out, fetch_mock, llm_with_tools


def test_pending_repolls_deterministically_without_llm():
    out, fetch_mock, llm_with_tools = _run_pending_medic()
    assert fetch_mock.invoke.called, "deterministic poll did not fetch the CI state"
    assert not llm_with_tools.invoke.called, \
        "LLM invoked on a pending poll — it would skip the re-fetch (the bug)"
    assert out["next_step"] == "medic", "pending must re-poll (route back to medic), not FINISH"
    assert out.get("mission_status", "") != "verified"
    assert out["ci_poll_attempt"] == 1
