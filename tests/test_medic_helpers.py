"""Unit tests for the Medic's deterministic, anti-hallucination helpers.

These parse message history at the Python layer so the LLM never has to "discover"
errors — the core defence against hallucinated fixes.
"""
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage

from agents.medic import (
    _extract_validation_summary,
    _owner_of_file,
    _latest_autovalidation_failure,
)


def _ai_validate_call(filename, call_id):
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "validate_generated_code",
            "args": {"filename": filename},
            "id": call_id,
            "type": "tool_call",
        }],
    )


class TestExtractValidationSummary:
    def test_failed_file_captured_with_detail(self):
        msgs = [
            _ai_validate_call("scripts/pipe.py", "c1"),
            ToolMessage(content="VALIDATION FAILED — fix before proceeding:\nF841 unused",
                        tool_call_id="c1"),
        ]
        result = _extract_validation_summary(msgs)
        assert result["scripts/pipe.py"][0] == "FAILED"
        assert "F841" in result["scripts/pipe.py"][1]

    def test_clean_file_captured_without_detail(self):
        msgs = [
            _ai_validate_call("sql/setup_trino.sql", "c2"),
            ToolMessage(content="CLEAN: no issues found", tool_call_id="c2"),
        ]
        result = _extract_validation_summary(msgs)
        assert result["sql/setup_trino.sql"] == ("CLEAN", "")

    def test_latest_result_supersedes_earlier(self):
        # same file FAILED then CLEAN on a later attempt → CLEAN wins
        msgs = [
            _ai_validate_call("scripts/p.py", "c1"),
            ToolMessage(content="VALIDATION FAILED — fix before proceeding:\nx", tool_call_id="c1"),
            _ai_validate_call("scripts/p.py", "c2"),
            ToolMessage(content="CLEAN", tool_call_id="c2"),
        ]
        assert _extract_validation_summary(msgs)["scripts/p.py"][0] == "CLEAN"

    def test_toolmessage_without_known_call_id_ignored(self):
        msgs = [ToolMessage(content="VALIDATION FAILED", tool_call_id="orphan")]
        assert _extract_validation_summary(msgs) == {}

    def test_unrelated_tool_calls_ignored(self):
        msgs = [
            AIMessage(content="", tool_calls=[{
                "name": "push_to_github", "args": {}, "id": "g1", "type": "tool_call"}]),
            ToolMessage(content="VALIDATION FAILED", tool_call_id="g1"),
        ]
        assert _extract_validation_summary(msgs) == {}


class TestOwnerOfFile:
    def test_python_owned_by_architect(self):
        assert _owner_of_file("scripts/pipe_crm_us_to_azure.py") == "architect"

    def test_sql_owned_by_architect(self):
        assert _owner_of_file("sql/setup_trino.sql") == "architect"

    def test_dashboard_owned_by_architect(self):
        assert _owner_of_file("dashboards/monitoring_specs.json") == "architect"

    def test_requirements_owned_by_architect(self):
        assert _owner_of_file("requirements.txt") == "architect"

    def test_k8s_owned_by_infra(self):
        assert _owner_of_file("k8s/job.yaml") == "infra"

    def test_dockerfile_owned_by_infra(self):
        assert _owner_of_file("Dockerfile") == "infra"

    def test_terraform_owned_by_infra(self):
        assert _owner_of_file("terraform/main.tf") == "infra"


class TestLatestAutovalidationFailure:
    def test_recovers_filename_from_marker(self):
        msgs = [HumanMessage(content=(
            "AUTO-VALIDATION FAILED — fix these errors in "
            "'scripts/pipe_crm_us_to_azure.py':\nSTORAGE: ..."))]
        assert _latest_autovalidation_failure(msgs) == "scripts/pipe_crm_us_to_azure.py"

    def test_returns_most_recent_when_multiple(self):
        msgs = [
            HumanMessage(content="AUTO-VALIDATION FAILED — fix these errors in 'a.py':"),
            HumanMessage(content="AUTO-VALIDATION FAILED — fix these errors in 'b.py':"),
        ]
        assert _latest_autovalidation_failure(msgs) == "b.py"

    def test_returns_empty_when_no_marker(self):
        msgs = [HumanMessage(content="all good, nothing failed")]
        assert _latest_autovalidation_failure(msgs) == ""


class TestResolveRelevantStandards:
    """Post-codegen: standards for code-owned artifacts are fetched on demand —
    architect/infra no longer pre-load them into collected_specs."""

    _INDICATORS = {
        "arch_standard_python": ["scripts/", "pandas"],
        "infra_standard_k8s": ["k8s/", "configmap"],
        "infra_standard_cicd": [".github/", "workflow"],
    }

    def test_preloaded_standard_comes_from_collected_specs(self):
        from agents.medic import _resolve_relevant_standards
        fetched = []
        out = _resolve_relevant_standards(
            "validation failed in scripts/pipe_x.py pandas chunk",
            self._INDICATORS, {"arch_standard_python": "THE PYTHON STANDARD"},
            fetch=lambda q: fetched.append(q) or "should not be used",
        )
        assert out == {"arch_standard_python": "THE PYTHON STANDARD"}
        assert fetched == []  # no Pinecone round-trip for pre-loaded keys

    def test_code_owned_standard_is_fetched_on_demand(self):
        from agents.medic import _resolve_relevant_standards
        out = _resolve_relevant_standards(
            "kubectl apply failed for k8s/job.yaml configmap missing",
            self._INDICATORS, {},  # collected_specs no longer carries k8s standard
            fetch=lambda q: "K8S STANDARD CONTENT",
        )
        assert out == {"infra_standard_k8s": "K8S STANDARD CONTENT"}

    def test_unmatched_error_fetches_nothing(self):
        from agents.medic import _resolve_relevant_standards
        out = _resolve_relevant_standards(
            "terraform state lock error", self._INDICATORS, {},
            fetch=lambda q: "X",
        )
        assert out == {}

    def test_fetch_failure_and_empty_results_are_skipped(self):
        from agents.medic import _resolve_relevant_standards

        def boom(q):
            raise RuntimeError("pinecone down")

        assert _resolve_relevant_standards(
            "workflow .github/ failed", self._INDICATORS, {}, fetch=boom) == {}
        assert _resolve_relevant_standards(
            "workflow .github/ failed", self._INDICATORS, {},
            fetch=lambda q: "No relevant guidelines found.") == {}
