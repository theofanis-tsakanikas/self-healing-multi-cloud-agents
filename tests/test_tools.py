import json
from unittest.mock import MagicMock, patch

# agents/tools.py initialises Pinecone, OpenAI, and OpenAIEmbeddings at module
# level. Patch those constructors before the import so no credentials are needed.
with patch("pinecone.Pinecone", MagicMock()), \
     patch("openai.OpenAI", MagicMock()), \
     patch("langchain_openai.OpenAIEmbeddings", MagicMock()):
    from agents.tools import _normalize_handoff_agent, request_fix


class TestNormalizeHandoffAgent:

    def test_infrastructure_maps_to_infra(self):
        assert _normalize_handoff_agent("infrastructure") == "infra"

    def test_terraform_maps_to_infra(self):
        assert _normalize_handoff_agent("terraform") == "infra"

    def test_architect_maps_to_architect(self):
        assert _normalize_handoff_agent("architect") == "architect"


class TestRequestFix:

    def test_returns_rejected_by_medic_status(self):
        result = request_fix.invoke({
            "target_agent": "infra",
            "issue_description": "Terraform apply failed with 403",
            "suggested_fix": "Verify AWS IAM permissions",
        })
        data = json.loads(result)
        assert data["status"] == "REJECTED_BY_MEDIC"

    def test_infra_target_agent_is_normalised(self):
        result = request_fix.invoke({
            "target_agent": "infrastructure",
            "issue_description": "K8s pod in CrashLoopBackOff",
            "suggested_fix": "Fix the image pull policy",
        })
        data = json.loads(result)
        assert data["target_agent"] == "infra"

    def test_architect_target_agent_is_normalised(self):
        result = request_fix.invoke({
            "target_agent": "architect",
            "issue_description": "SQL DDL syntax error",
            "suggested_fix": "Fix the CREATE TABLE statement",
        })
        data = json.loads(result)
        assert data["target_agent"] == "architect"
