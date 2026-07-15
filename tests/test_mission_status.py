"""Terminal mission-status contract: the graph's outcome flag drives the entry-point
exit code (GitHub Action red/green), the Streamlit banner, and the LangSmith root-run
status. Only mission_status == "verified" is success — every other termination fails.
"""
import pytest
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage, HumanMessage

from agents.supervisor import supervisor_node
from main import MissionFailedError, _consume_stream, mission_failure_summary


def _make_state(**kwargs):
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
    with patch("agents.supervisor.get_llm", return_value=MagicMock()):
        yield


def _stream(updates):
    """Real generator so _consume_stream's stream.throw() reaches a live yield point."""
    for u in updates:
        yield u


class TestSupervisorTerminalStatus:

    def test_verified_finish_preserves_mission_status(self):
        # Success is deterministic: the Medic sets mission_status="verified" from a confirmed green
        # CI run, then the supervisor routes to FINISH. No LLM-prose token can manufacture success.
        state = _make_state(
            last_agent="medic",
            mission_status="verified",
            messages=[AIMessage(content="...Everything looks green!")],
        )
        result = supervisor_node(state)
        assert result["next_step"] == "FINISH"
        # the supervisor must not downgrade the deterministic verified status
        assert result.get("mission_status", state["mission_status"]) == "verified"

    def test_escalated_finish_does_not_claim_verified(self):
        state = _make_state(
            last_agent="medic",
            fix_loop_escalated=True,
            mission_status="escalated",
            messages=[HumanMessage(content="Self-healing could not resolve this")],
        )
        result = supervisor_node(state)
        assert result["next_step"] == "FINISH"
        # the escalation route must NOT overwrite the failure status with success
        assert result.get("mission_status", "escalated") != "verified"


class TestConsumeStream:

    def test_verified_run_completes_without_raising(self):
        updates = [
            {"medic": {"mission_status": "verified"}},
            {"supervisor": {"next_step": "FINISH"}},
        ]
        assert _consume_stream(_stream(updates)) == "verified"

    def test_escalated_run_raises_mission_failed(self):
        updates = [
            {"medic": {"mission_status": "escalated"}},
            {"supervisor": {"next_step": "FINISH"}},
        ]
        with pytest.raises(MissionFailedError, match="escalated"):
            _consume_stream(_stream(updates))

    def test_ci_unverified_run_raises_mission_failed(self):
        updates = [
            {"medic": {"mission_status": "ci_unverified"}},
            {"supervisor": {"next_step": "FINISH"}},
        ]
        with pytest.raises(MissionFailedError, match="ci_unverified"):
            _consume_stream(_stream(updates))

    def test_finish_without_any_status_is_failure(self):
        """LLM-fallback FINISH (no explicit verification) must fail — fail-safe default."""
        updates = [{"supervisor": {"next_step": "FINISH"}}]
        with pytest.raises(MissionFailedError, match="unset"):
            _consume_stream(_stream(updates))

    def test_verified_in_same_update_as_finish(self):
        updates = [{"supervisor": {"next_step": "FINISH", "mission_status": "verified"}}]
        assert _consume_stream(_stream(updates)) == "verified"

    def test_non_finish_routing_never_raises(self):
        updates = [
            {"supervisor": {"next_step": "architect"}},
            {"architect": {"architect_status": "completed"}},
            {"supervisor": {"next_step": "infra"}},
        ]
        assert _consume_stream(_stream(updates)) == ""

    def test_throw_reaches_the_generator(self):
        """The failure must be thrown INTO the stream (marks the LangSmith root run as
        error), not just raised in the caller."""
        seen = {}

        def gen():
            try:
                yield {"supervisor": {"next_step": "FINISH"}}
            except MissionFailedError:
                seen["inside"] = True
                raise

        with pytest.raises(MissionFailedError):
            _consume_stream(gen())
        assert seen.get("inside") is True


class TestMissionFailureSummary:

    def test_known_statuses_have_specific_summaries(self):
        assert "3 fix rounds" in mission_failure_summary("escalated")
        assert "UNKNOWN" in mission_failure_summary("ci_unverified")

    def test_unknown_status_falls_back_to_unverified_default(self):
        assert mission_failure_summary("") == mission_failure_summary("weird_future_value")
