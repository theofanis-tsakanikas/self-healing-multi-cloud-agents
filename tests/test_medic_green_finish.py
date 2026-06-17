"""
Regression: on a green CI verification the medic must ANSWER the fetch tool_call (append its
ToolMessage) before breaking. Otherwise it returns an AIMessage with a dangling tool_call, the
graph routes to the ToolNode (EXECUTE_TOOLS) to satisfy it instead of to the supervisor, and the
run loops medic↔EXECUTE_TOOLS forever — never reaching the mission_status="verified" FINISH, so a
SUCCESSFUL deploy dies at the recursion limit (observed run 27451648935, 2026-06-13).
"""
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agents.medic as medic_mod
from agents.medic import medic_node


def _green_state():
    return {
        "task": "verify", "messages": [HumanMessage(content="infra completed")],
        "error_log": "", "project_id": "PIPE-X", "config_path": "",
        "target_infra": "aws", "written_files": ["scripts/x.py"],
        "infra_provisioned": True, "infra_status": "completed",
        "architect_status": "completed", "schema_discovered": True,
        "github_done": True, "last_push_sha": "abc1234", "medic_fix_requested": False,
        "ci_poll_attempt": 0, "fix_attempt": 0, "last_fix_signature": "",
        "medic_fix_target": "", "mission_status": "",
    }


def _run_green_medic():
    green = "No failed jobs found in run 123. Everything looks green!"
    fetch_mock = MagicMock()
    fetch_mock.name = "fetch_github_action_logs"
    fetch_mock.invoke.return_value = green

    ai = AIMessage(
        content="",
        tool_calls=[{"name": "fetch_github_action_logs", "args": {}, "id": "call_green"}],
    )
    llm_with_tools = MagicMock()
    llm_with_tools.invoke.return_value = ai
    llm = MagicMock()
    llm.bind_tools.return_value = llm_with_tools

    with patch.object(medic_mod, "get_llm", return_value=llm), \
         patch.object(medic_mod, "fetch_github_action_logs", fetch_mock), \
         patch.object(medic_mod, "store_architectural_insight", MagicMock()):
        return medic_node(_green_state())


def test_green_ci_verifies_and_routes_to_supervisor():
    out = _run_green_medic()
    assert out["mission_status"] == "verified"
    assert out["next_step"] == "supervisor"


def test_green_ci_leaves_no_dangling_tool_call():
    # The whole bug: a dangling (unanswered) tool_call at the end routes the graph to the ToolNode,
    # not the supervisor. With the DETERMINISTIC CI poll a green run is decided in Python (the LLM
    # is never invoked for a green run), so there is no fetch tool_call at all — and the last
    # message must NOT be an AIMessage carrying an unanswered tool_call.
    out = _run_green_medic()
    answered = {m.tool_call_id for m in out["messages"] if isinstance(m, ToolMessage)}
    for m in out["messages"]:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                assert tc["id"] in answered, "green fetch tool_call left unanswered → EXECUTE_TOOLS loop"
    _last = out["messages"][-1]
    assert not (isinstance(_last, AIMessage) and _last.tool_calls), \
        "dangling tool_call at end → EXECUTE_TOOLS loop instead of supervisor"
