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


# --- the fix-prompt injects the CURRENT file content so the patch targets real text ---
def test_inject_current_file_contents_uses_real_text(tmp_path, monkeypatch):
    """The LLM patched blind against the standard's `<pipeline_id>` placeholder → no-op. The fix
    injects the REAL on-disk content so `old` matches (e.g. the wrong `postgres_password`)."""
    import agents.infra as infra
    (tmp_path / "terraform").mkdir()
    (tmp_path / "terraform" / "main.tf").write_text(
        'resource "databricks_secret" "db_password" {\n'
        '  key   = "postgres_password"\n'
        '  scope = databricks_secret_scope.pipeline.name\n}\n'
    )
    monkeypatch.setattr(infra, "REPO_ROOT", tmp_path)

    hc = "Target: the pipeline Terraform (terraform/main.tf) — Secret does not exist key: db_password"
    block = infra._inject_current_file_contents(hc)
    assert "postgres_password" in block          # the REAL wrong value is now visible to patch
    assert "CURRENT ON-DISK CONTENT" in block

    # architect-owned files (.py / sql / dashboards / requirements) are never injected by infra
    assert infra._inject_current_file_contents("fix scripts/pipe_x.py now") == ""
    # no infra path named → nothing injected
    assert infra._inject_current_file_contents("a generic message with no file path") == ""


# --- deterministic heal completion: a clean patch forces terraform apply + push ---
def _run_heal_with_llm_response(monkeypatch, provider, tool_calls):
    """Drive infra_node with a mocked LLM that emits exactly `tool_calls`, mocking the side-effect
    tools so nothing touches real infra. Returns (recorded_tool_calls, output_state)."""
    import agents.infra as infra
    import agents.tools as tools
    from langchain_core.messages import AIMessage

    recorded: list[str] = []

    def _tool(name, ret):
        m = MagicMock()
        def _inv(args):
            recorded.append(name)
            return ret
        m.invoke.side_effect = _inv
        return m

    def fake_bind_tools(t, **kw):
        bound = MagicMock()
        bound.invoke.return_value = AIMessage(content="", tool_calls=tool_calls)
        return bound

    mock_llm = MagicMock()
    mock_llm.bind_tools.side_effect = fake_bind_tools

    monkeypatch.setattr(infra, "get_llm", lambda **kw: mock_llm)
    monkeypatch.setattr(infra, "build_databricks_infra_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(infra, "build_infra_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(infra, "patch_project_file",
                        _tool("patch", "PATCH APPLIED to 'terraform/main.tf'.\nApplied:\n  key change\nSkipped:\n  (none)"))
    monkeypatch.setattr(infra, "execute_terraform",
                        _tool("apply", "SUCCESS: Terraform apply\nApply complete! Resources: 1 changed."))
    monkeypatch.setattr(infra, "push_to_github",
                        _tool("push", "STATUS: SUCCESS\nSHA: " + "a" * 40))
    # keep auto-validation deterministic + side-effect-free
    monkeypatch.setattr(tools, "validate_generated_code", MagicMock(invoke=lambda a: "CLEAN ✓"))

    out = infra.infra_node(_heal_state(provider))
    return recorded, out


_PATCH_CALL = [{
    "name": "patch_project_file", "id": "c1",
    "args": {"filename": "terraform/main.tf",
             "replacements": [{"old": 'key = "postgres_password"', "new": 'key = "db_password"'}]},
}]


def test_databricks_heal_forces_apply_and_push_after_patch(monkeypatch):
    # The failure mode: the LLM emits ONLY the patch (no apply/push). The node must force both.
    recorded, out = _run_heal_with_llm_response(monkeypatch, "databricks", _PATCH_CALL)
    assert "patch" in recorded
    assert "apply" in recorded, "terraform apply must be forced after a clean patch"
    assert "push" in recorded, "push must be forced after apply (despite prior github_done=True)"
    assert out["github_done"] is True
    assert out["last_push_sha"] == "a" * 40


def test_object_storage_heal_does_not_force_terraform_apply(monkeypatch):
    # AWS/Azure/GCP: a clean patch must NOT trigger a forced terraform apply (deploy re-applies k8s).
    recorded, _ = _run_heal_with_llm_response(monkeypatch, "kubernetes", _PATCH_CALL)
    assert "patch" in recorded
    assert "apply" not in recorded, "object-storage heal must not force terraform apply"
