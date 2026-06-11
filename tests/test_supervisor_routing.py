import pytest
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage

from agents.supervisor import supervisor_node, _normalize_medic_target


def _make_state(**kwargs):
    """Minimal AgentState dict for supervisor tests."""
    base = {
        "task": "test",
        "messages": [HumanMessage(content="ok")],
        "error_log": "",
        "project_id": "test_proj",
        "config_path": "",
        "target_infra": "aws",
        "next_step": "",
        "last_agent": "None",
        "raw_configs": {},
        "written_files": [],
        "infra_provisioned": False,
        "infra_status": "pending",
        "architect_status": "pending",
        "collected_specs": {},
        "schema_discovered": False,
        "github_done": False,
        "last_push_sha": "",
        "medic_fix_requested": False,
    }
    base.update(kwargs)
    return base


@pytest.fixture(autouse=True)
def mock_get_llm():
    """Prevent any real LLM instantiation or API calls in all supervisor tests."""
    with patch("agents.supervisor.get_llm", return_value=MagicMock()):
        yield


class TestRuleAArchitect:

    def test_architect_completed_routes_to_infra(self):
        state = _make_state(last_agent="architect", architect_status="completed")
        assert supervisor_node(state)["next_step"] == "infra"

    def test_architect_pending_stays_with_architect(self):
        state = _make_state(last_agent="architect", architect_status="pending")
        assert supervisor_node(state)["next_step"] == "architect"

    def test_architect_agent_error_routes_to_medic_and_clears_flag(self):
        state = _make_state(last_agent="architect", agent_error=True,
                            architect_status="completed")
        result = supervisor_node(state)
        # agent_error takes precedence over a 'completed' status, and is cleared.
        assert result["next_step"] == "medic"
        assert result["agent_error"] is False

    def test_architect_error_keyword_routes_to_medic(self):
        state = _make_state(
            last_agent="architect", architect_status="pending",
            messages=[AIMessage(content="Traceback: SyntaxError in script")],
        )
        assert supervisor_node(state)["next_step"] == "medic"


class TestRuleBInfra:

    def test_infra_completed_routes_to_medic(self):
        state = _make_state(last_agent="infra", infra_status="completed")
        assert supervisor_node(state)["next_step"] == "medic"

    def test_infra_pending_stays_with_infra(self):
        state = _make_state(last_agent="infra", infra_status="pending")
        assert supervisor_node(state)["next_step"] == "infra"

    def test_infra_agent_error_routes_to_medic_and_clears_flag(self):
        state = _make_state(last_agent="infra", agent_error=True)
        result = supervisor_node(state)
        assert result["next_step"] == "medic"
        assert result["agent_error"] is False


class TestRuleCMedic:

    def test_medic_alignment_ok_routes_to_finish(self):
        # Supervisor scans AIMessages only — a HumanMessage would be ignored.
        state = _make_state(
            last_agent="medic",
            messages=[AIMessage(content="ALIGNMENT_OK")],
            next_step="",
        )
        assert supervisor_node(state)["next_step"] == "FINISH"

    def test_fix_loop_escalated_routes_to_finish_and_clears(self):
        state = _make_state(last_agent="medic", fix_loop_escalated=True,
                            medic_fix_target="architect")
        result = supervisor_node(state)
        # Escalation wins over the lingering fix target — no loop back in.
        assert result["next_step"] == "FINISH"
        assert result["fix_loop_escalated"] is False

    def test_medic_fix_target_architect_resets_status_and_consumes_target(self):
        state = _make_state(last_agent="medic", medic_fix_target="architect",
                            architect_status="completed")
        result = supervisor_node(state)
        assert result["next_step"] == "architect"
        assert result["architect_status"] == "pending"
        assert result["medic_fix_target"] == ""

    def test_medic_fix_target_infra_resets_status_and_consumes_target(self):
        state = _make_state(last_agent="medic", medic_fix_target="infra",
                            infra_status="completed")
        result = supervisor_node(state)
        assert result["next_step"] == "infra"
        assert result["infra_status"] == "pending"
        assert result["medic_fix_target"] == ""


class TestNormalizeMedicTarget:
    def test_architect_keywords(self):
        assert _normalize_medic_target("architect") == "architect"
        assert _normalize_medic_target("fix the code logic") == "architect"

    def test_infra_keywords(self):
        assert _normalize_medic_target("terraform") == "infra"
        assert _normalize_medic_target("k8s manifest") == "infra"

    def test_unknown_returns_none(self):
        assert _normalize_medic_target("something else") is None
