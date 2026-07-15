"""INTEGRATION regression: a CI-runtime failure heal must be HONOURED when the evidence_quote comes
from THIS turn's auto-poll fetch (not prior history).

The verification-phase auto-poll appends the fetched CI log to the LOCAL accumulator, never to the
node's input `state["messages"]`. If the provenance check only searches `state["messages"]`, a
legitimate quote of that fresh log is judged a hallucination and the heal is silently dropped — the
run ends unverified on a REAL, fixable CI failure. This drives medic_node end-to-end so it actually
catches that (the unit test on the helper alone could not)."""
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

import agents.medic as medic_mod
from agents.medic import medic_node

_FAIL_LOG = (
    "FAILED: deploy job failed.\n"
    "Traceback (most recent call last):\n"
    '  File "scripts/pipe_x.py", line 42, in <module>\n'
    "    chunk['campaign']\n"
    "KeyError: 'campaign'\n"
    "exit code 1"
)


def _verification_state():
    # Prior history has NO CI log — the failing log only arrives via this turn's auto-poll.
    return {
        "task": "verify", "messages": [HumanMessage(content="infra completed, awaiting CI")],
        "error_log": "", "project_id": "PIPE-X-20260701-1200", "config_path": "",
        "target_infra": "aws", "written_files": ["scripts/pipe_x.py"],
        "infra_provisioned": True, "infra_status": "completed",
        "architect_status": "completed", "schema_discovered": True,
        "github_done": True, "last_push_sha": "deadbeef", "medic_fix_requested": False,
        "ci_poll_attempt": 0, "fix_attempt": 0, "last_fix_signature": "",
        "medic_fix_target": "", "mission_status": "",
    }


def test_ci_runtime_heal_honoured_when_evidence_is_from_this_turns_fetch():
    fetch_mock = MagicMock()
    fetch_mock.name = "fetch_github_action_logs"
    fetch_mock.invoke.return_value = _FAIL_LOG  # a REAL failure → falls through to the LLM

    # The LLM quotes the just-fetched log verbatim ("KeyError: 'campaign'" contains the "Error:" marker).
    ai = AIMessage(content="diagnosing", tool_calls=[{
        "name": "request_fix",
        "args": {
            "target_agent": "architect",
            "issue_description": "KeyError on campaign column",
            "suggested_fix": "resolve 'campaign' to the real column 'campaign_id'",
            "evidence_quote": "KeyError: 'campaign'",
        },
        "id": "c1",
    }])
    llm_with_tools = MagicMock()
    llm_with_tools.invoke.return_value = ai
    llm = MagicMock()
    llm.bind_tools.return_value = llm_with_tools

    with patch.object(medic_mod, "get_llm", return_value=llm), \
         patch.object(medic_mod, "fetch_github_action_logs", fetch_mock), \
         patch.object(medic_mod, "store_architectural_insight", MagicMock()), \
         patch.object(medic_mod.time, "sleep", MagicMock()):
        out = medic_node(_verification_state())

    # The heal MUST be honoured — evidence came from this turn's fetch, which is real.
    assert out.get("medic_fix_target") == "architect", (
        f"CI-runtime heal was dropped (medic_fix_target={out.get('medic_fix_target')!r}); "
        f"provenance likely searched the wrong message list. next_step={out.get('next_step')!r}"
    )
    assert out.get("mission_status", "") != "verified"
