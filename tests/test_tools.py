import json

# Heavy SDK constructors are patched globally in conftest.py before collection,
# so a plain import is safe and credential-free.
from agents.tools import _normalize_handoff_agent, request_fix

# A snippet containing a recognised error marker, required by request_fix.
_EVIDENCE = "VALIDATION FAILED — fix before proceeding:\nF841 unused variable"


class TestNormalizeHandoffAgent:

    def test_infrastructure_maps_to_infra(self):
        assert _normalize_handoff_agent("infrastructure") == "infra"

    def test_terraform_maps_to_infra(self):
        assert _normalize_handoff_agent("terraform") == "infra"

    def test_architect_maps_to_architect(self):
        assert _normalize_handoff_agent("architect") == "architect"


class TestRequestFix:

    def test_valid_evidence_returns_rejected_by_medic(self):
        result = request_fix.invoke({
            "target_agent": "infra",
            "issue_description": "Terraform apply failed with 403",
            "suggested_fix": "Verify AWS IAM permissions",
            "evidence_quote": "Error: AccessDenied 403",
        })
        data = json.loads(result)
        assert data["status"] == "REJECTED_BY_MEDIC"

    def test_infra_target_agent_is_normalised(self):
        result = request_fix.invoke({
            "target_agent": "infrastructure",
            "issue_description": "K8s pod in CrashLoopBackOff",
            "suggested_fix": "Fix the image pull policy",
            "evidence_quote": _EVIDENCE,
        })
        data = json.loads(result)
        assert data["target_agent"] == "infra"

    def test_architect_target_agent_is_normalised(self):
        result = request_fix.invoke({
            "target_agent": "architect",
            "issue_description": "SQL DDL syntax error",
            "suggested_fix": "Fix the CREATE TABLE statement",
            "evidence_quote": _EVIDENCE,
        })
        data = json.loads(result)
        assert data["target_agent"] == "architect"

    def test_empty_evidence_is_rejected_as_tool_error(self):
        result = request_fix.invoke({
            "target_agent": "architect",
            "issue_description": "I think the SQL looks wrong",
            "suggested_fix": "rewrite it",
            "evidence_quote": "",
        })
        data = json.loads(result)
        assert data["status"] == "TOOL_ERROR"

    def test_evidence_without_error_marker_is_rejected(self):
        # No marker (VALIDATION FAILED / Error: / Traceback …) → anti-hallucination guard.
        result = request_fix.invoke({
            "target_agent": "architect",
            "issue_description": "the catalog name seems off to me",
            "suggested_fix": "change it",
            "evidence_quote": "the file uses azure_catalog which I think is wrong",
        })
        data = json.loads(result)
        assert data["status"] == "TOOL_ERROR"
