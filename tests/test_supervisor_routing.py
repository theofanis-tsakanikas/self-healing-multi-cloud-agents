import pytest
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage

from agents.supervisor import supervisor_node


def _make_state(**kwargs):
    """Minimal AgentState dict for supervisor tests."""
    base = {
        "task": "test",
        "messages": [HumanMessage(content="ok")],
        "generated_code": "",
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


class TestSupervisorRouting:

    def test_architect_completed_routes_to_infra(self):
        state = _make_state(last_agent="architect", architect_status="completed")
        result = supervisor_node(state)
        assert result["next_step"] == "infra"

    def test_architect_pending_stays_with_architect(self):
        state = _make_state(last_agent="architect", architect_status="pending")
        result = supervisor_node(state)
        assert result["next_step"] == "architect"

    def test_infra_completed_routes_to_medic(self):
        state = _make_state(last_agent="infra", infra_status="completed")
        result = supervisor_node(state)
        assert result["next_step"] == "medic"

    def test_infra_pending_stays_with_infra(self):
        state = _make_state(last_agent="infra", infra_status="pending")
        result = supervisor_node(state)
        assert result["next_step"] == "infra"

    def test_medic_alignment_ok_routes_to_finish(self):
        # "alignment_ok" is the lowercased form matched by supervisor_node
        state = _make_state(
            last_agent="medic",
            messages=[HumanMessage(content="ALIGNMENT_OK")],
            next_step="",
        )
        result = supervisor_node(state)
        assert result["next_step"] == "FINISH"
