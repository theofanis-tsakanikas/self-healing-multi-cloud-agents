"""build_initial_state must cover EVERY AgentState field — so the CLI and Streamlit entry points,
which both use it, can never drift on which keys are set (a bare state[key] read would KeyError in
one path but not the other)."""
from agents.state import AgentState, build_initial_state


def test_factory_covers_every_agent_state_field():
    state = build_initial_state(
        project_id="proj-1",
        task="do the thing",
        raw_configs={"pipeline": {}, "database": {}, "rules": {}, "infrastructure": {}},
        target_infra="s3",
    )
    assert set(state.keys()) == set(AgentState.__annotations__.keys()), (
        "build_initial_state and AgentState disagree: "
        f"missing={set(AgentState.__annotations__) - set(state)}, "
        f"extra={set(state) - set(AgentState.__annotations__)}"
    )


def test_factory_sets_sane_terminal_defaults():
    state = build_initial_state("p", "t", {}, target_infra="aws")
    assert state["mission_status"] == "" and state["next_step"] == "" and state["last_agent"] == "None"
    assert state["ci_poll_attempt"] == 0 and state["agent_error"] is False
