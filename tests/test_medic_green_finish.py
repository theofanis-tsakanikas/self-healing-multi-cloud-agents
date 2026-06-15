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
    # The whole bug: an unanswered tool_call routes to the ToolNode, not the supervisor.
    # The CI poll is now deterministic (Python), so verification no longer goes through an LLM
    # tool_call at all — but the invariant still holds and is STRONGER: the medic's last message
    # must not be an AIMessage carrying an unanswered tool_call (which graph.should_continue would
    # route to EXECUTE_TOOLS instead of the supervisor).
    out = _run_green_medic()
    answered = {m.tool_call_id for m in out["messages"] if isinstance(m, ToolMessage)}
    for m in out["messages"]:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                assert tc["id"] in answered, "fetch tool_call left unanswered → EXECUTE_TOOLS loop"
    last = out["messages"][-1]
    assert not (isinstance(last, AIMessage) and last.tool_calls), \
        "last message must not carry an unanswered tool_call → would route to EXECUTE_TOOLS"


def test_pending_ci_repolls_in_python_without_the_llm():
    # The CI-triggered failure: the deploy was QUEUED, so the auto-poll returns PENDING. The medic
    # must re-poll deterministically (next_step='medic', counter++) WITHOUT consulting the LLM —
    # gpt-4o-mini used to skip the re-fetch (prompt: "tell the user you are waiting"), stalling the
    # loop so the supervisor's LLM fallback FINISHed with mission_status unset on a fine deploy.
    pending = "PENDING: Run 27529330898 still in progress (status: queued). Retry later."
    fetch_mock = MagicMock()
    fetch_mock.name = "fetch_github_action_logs"
    fetch_mock.invoke.return_value = pending
    llm_with_tools = MagicMock()
    llm = MagicMock()
    llm.bind_tools.return_value = llm_with_tools

    with patch.object(medic_mod, "get_llm", return_value=llm), \
         patch.object(medic_mod, "fetch_github_action_logs", fetch_mock), \
         patch.object(medic_mod, "time", MagicMock()), \
         patch.object(medic_mod, "store_architectural_insight", MagicMock()):
        out = medic_node(_green_state())

    assert out["next_step"] == "medic"                  # re-poll, not FINISH
    assert out.get("ci_poll_attempt", 0) == 1           # counter advanced
    assert out.get("mission_status", "") != "verified"  # not a false green
    llm_with_tools.invoke.assert_not_called()           # the LLM was NOT consulted on pending
    # The fetch MUST be invoked with the REQUIRED project_id (no default) — passing only
    # head_sha raises a pydantic 'Field required' and crashes the workflow engine.
    _args = fetch_mock.invoke.call_args.args[0]
    assert "project_id" in _args and _args["project_id"] == "PIPE-X"
    assert _args["head_sha"] == "abc1234"
