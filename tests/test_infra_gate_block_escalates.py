"""A pre-push SECURITY GATE block must ESCALATE (agent_error → supervisor routes to medic), not loop.

Drives infra_node with an LLM that calls push_to_github; the (mocked) push returns the gate-failure
string. Asserts the node surfaces agent_error and does NOT mark the deploy successful."""
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

import agents.infra as infra_mod  # noqa: E402
from agents.infra import infra_node  # noqa: E402


def _heal_state() -> dict:
    return {
        "task": "t",
        "messages": [HumanMessage(content="fix it")],
        "project_id": "pipe_x",
        "raw_configs": {
            "pipeline": {"pipeline_id": "pipe_x", "cloud_provider": "aws"},
            "infrastructure": {"provider": "kubernetes"},
            "database": {"db_type": "postgres"},
        },
        "collected_specs": {"infra_standard_iac": "IAC STANDARD TEXT"},
        "written_files": ["terraform/main.tf", "k8s/job.yaml", "Dockerfile"],
        "infra_provisioned": True,
        "github_done": True,
        "medic_fix_requested": True,
        "healing_context": "Fix terraform/main.tf.",
        "last_agent": "medic",
        "ecr_repository_url": "x.dkr.ecr.eu-central-1.amazonaws.com/repo",
    }


def test_security_gate_block_on_push_sets_agent_error():
    gate_err = "Error: SECURITY GATE FAILED — refusing to push 1 HIGH finding(s): TF_PUBLIC_DB @ main.tf"
    push_mock = MagicMock()
    push_mock.name = "push_to_github"
    push_mock.invoke.return_value = gate_err

    def fake_bind_tools(tools, **kw):
        bound = MagicMock()
        bound.invoke.return_value = AIMessage(
            content="pushing",
            tool_calls=[{"name": "push_to_github", "args": {"project_id": "pipe_x", "commit_message": "deploy"}, "id": "c1"}],
        )
        return bound

    llm = MagicMock()
    llm.bind_tools.side_effect = fake_bind_tools

    with patch.object(infra_mod, "get_llm", return_value=llm), \
         patch.object(infra_mod, "push_to_github", push_mock):
        out = infra_node(_heal_state())

    assert out.get("agent_error") is True, f"gate block did not escalate: {out}"
    # Must NOT stay "completed" — else the medic auto-verifies the stale prior deploy (false success).
    assert out.get("infra_status") == "pending", f"stale completed status would cause false verify: {out}"
