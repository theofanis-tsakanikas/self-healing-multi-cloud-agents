"""
Regression for the Databricks infra-heal terraform RE-APPLY (2026-06-19).

When the Medic routes a CI-runtime infra failure back to the infra agent on a Databricks
pipeline (e.g. the secret-scope key mismatch), patch_project_file + push_to_github is NOT
enough: the deploy workflow does not run `terraform apply`, so the LIVE Databricks resource
(the secret scope) stays stale and the job fails identically on re-run. The infra node must
therefore also bind `execute_terraform` so the agent re-applies the fix.

CRITICAL — protect the validated AWS/Azure/GCP runs: an object-storage cloud heal keeps the
patch+push path (no execute_terraform added) — their infra heals are k8s/workflow files the
deploy workflow re-applies. The fix is gated to `is_databricks` only.
"""
import os
from unittest.mock import patch, MagicMock

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agents.infra import infra_node  # noqa: E402


def _heal_state(provider: str) -> dict:
    """A medic-triggered infra heal AFTER the first deploy (github_done) with a pending
    healing_context — the exact shape the supervisor hands infra for a CI-runtime fix."""
    infra_conf = {"provider": provider}
    return {
        "task": "t",
        "messages": [HumanMessage(content="fix it")],
        "project_id": "pipe_sales_orders_lakehouse",
        "raw_configs": {
            "pipeline": {"pipeline_id": "pipe_sales_orders_lakehouse", "cloud_provider": "aws"},
            "infrastructure": infra_conf,
            "database": {"db_type": "postgres"},
        },
        # standards already discovered → past the discovery gate
        "collected_specs": {"infra_standard_iac": "IAC STANDARD TEXT"},
        # first deploy already happened and terraform already applied once
        "written_files": [
            "terraform/main.tf", "terraform/providers.tf", "terraform/variables.tf",
            "terraform/outputs.tf", "terraform/terraform.tfvars",
            ".github/workflows/pipe_sales_orders_lakehouse_pipeline.yml",
            "k8s/00_namespaces.yaml", "k8s/trino_deployment.yaml", "k8s/grafana_deployment.yaml",
            "k8s/prometheus_deployment.yaml", "k8s/configmaps.yaml", "k8s/job.yaml", "Dockerfile",
        ],
        "infra_provisioned": True,     # tf_done → past the terraform-create gate
        "github_done": True,           # Scenario B / databricks push-phase
        "medic_fix_requested": True,
        "healing_context": "Fix terraform/main.tf: databricks_secret key must be 'db_password' "
                           "to match dbutils.secrets.get(scope, 'db_password').",
        "last_agent": "medic",
        "ecr_repository_url": "x.dkr.ecr.eu-central-1.amazonaws.com/repo",  # skip registry resolution
    }


def _bound_tools(provider: str) -> list[str]:
    """Run infra_node with a fully-mocked LLM and return the tool names it bound."""
    captured: dict = {}

    def fake_bind_tools(tools, **kw):
        captured["tools"] = [t.name for t in tools]
        bound = MagicMock()
        bound.invoke.return_value = AIMessage(content="ok")  # no tool_calls → node returns cleanly
        return bound

    mock_llm = MagicMock()
    mock_llm.bind_tools.side_effect = fake_bind_tools

    with patch("agents.infra.get_llm", return_value=mock_llm), \
         patch("agents.infra.build_databricks_infra_context", return_value="CTX"), \
         patch("agents.infra.build_infra_context", return_value="CTX"):
        infra_node(_heal_state(provider))
    return captured["tools"]


def test_databricks_heal_binds_execute_terraform():
    tools = _bound_tools("databricks")
    assert "execute_terraform" in tools, "Databricks infra heal must re-apply terraform"
    assert "patch_project_file" in tools   # added by the GATE 3 medic override
    assert "push_to_github" in tools


def test_object_storage_heal_does_not_add_execute_terraform():
    # AWS/Azure/GCP heal keeps patch+push (the deploy workflow re-applies k8s) — unchanged.
    tools = _bound_tools("kubernetes")
    assert "execute_terraform" not in tools, "object-storage heal must NOT change (protect validated clouds)"
    assert "patch_project_file" in tools
    assert "push_to_github" in tools
