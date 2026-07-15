"""Integration test of the compiled graph's routing spine — offline, no LLM.

Unit tests cover each node in isolation; this exercises the WIRING that connects them: the compiled
app's node set and the two conditional-edge functions (`should_continue`, `route_after_medic_tools`)
that unit tests of individual nodes never touch. A broken edge map or a regressed trailing-batch scan
is caught here before it can strand a run.
"""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph import app, build_app, route_after_medic_tools, should_continue


def test_compiled_graph_has_all_nodes():
    nodes = set(app.get_graph().nodes)
    for expected in ("supervisor", "architect", "infra", "medic", "execute_tools"):
        assert expected in nodes, f"graph is missing node {expected!r}"


def test_build_app_compiles_with_and_without_checkpointer():
    from langgraph.checkpoint.memory import MemorySaver

    assert build_app() is not None  # default path: no checkpointer, behavior unchanged
    assert build_app(checkpointer=MemorySaver()) is not None  # opt-in durable wiring compiles


def test_build_checkpointer_env_path_returns_a_real_durable_saver(monkeypatch, tmp_path):
    # Regression for the from_conn_string context-manager footgun: with LANGGRAPH_CHECKPOINT_DB set,
    # _build_checkpointer must return a real BaseCheckpointSaver (has get_tuple/put), NOT a context
    # manager — otherwise the first app.stream crashes. Default (unset) must stay None.
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from graph import _build_checkpointer

    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DB", raising=False)
    assert _build_checkpointer() is None

    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB", str(tmp_path / "ckpt.sqlite"))
    cp = _build_checkpointer()
    assert isinstance(cp, BaseCheckpointSaver)
    assert hasattr(cp, "get_tuple") and hasattr(cp, "put")
    assert build_app(checkpointer=cp) is not None


def test_should_continue_routes_tool_calls_to_execute_tools():
    ai = AIMessage(content="", tool_calls=[{"name": "request_fix", "args": {}, "id": "call_1"}])
    assert should_continue({"messages": [ai]}) == "execute_tools"


def test_should_continue_routes_plain_text_to_supervisor():
    assert should_continue({"messages": [AIMessage(content="waiting on CI")]}) == "supervisor"


def test_route_after_medic_tools_handoff_on_rejected_by_medic():
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "request_fix", "args": {}, "id": "c1"}]),
        ToolMessage(content='{"status": "REJECTED_BY_MEDIC", "target_agent": "infra"}', tool_call_id="c1"),
    ]
    assert route_after_medic_tools({"messages": msgs}) == "supervisor"


def test_route_after_medic_tools_continues_on_plain_tool_result():
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "query_vector_store", "args": {}, "id": "c2"}]),
        ToolMessage(content="🛡️ [OFFICIAL SPEC] some standard text", tool_call_id="c2"),
    ]
    assert route_after_medic_tools({"messages": msgs}) == "medic"


def test_route_after_medic_tools_ignores_stale_rejection():
    # A REJECTED_BY_MEDIC from an EARLIER turn must not re-fire — only the trailing tool batch counts.
    msgs = [
        ToolMessage(content='{"status": "REJECTED_BY_MEDIC"}', tool_call_id="old"),
        AIMessage(content="architect patched the file"),
        HumanMessage(content="next"),
        AIMessage(content="", tool_calls=[{"name": "query_vector_store", "args": {}, "id": "c3"}]),
        ToolMessage(content="🛡️ [OFFICIAL SPEC] fresh, clean result", tool_call_id="c3"),
    ]
    assert route_after_medic_tools({"messages": msgs}) == "medic"
