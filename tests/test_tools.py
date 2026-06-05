import json

import yaml

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

    def test_kubectl_immutable_job_error_is_accepted(self):
        # A genuine kubectl deploy failure must NOT be wrongly rejected — its text
        # carries no Python/CI marker, so the kubectl markers ('is invalid' /
        # 'Invalid value' / 'immutable') must let it through to the infra agent.
        result = request_fix.invoke({
            "target_agent": "infra",
            "issue_description": 'The Job "pipe-mkt-global-to-gcp-job" is invalid',
            "suggested_fix": "Use the two-step delete+apply deploy pattern",
            "evidence_quote": (
                'The Job "pipe-mkt-global-to-gcp-job" is invalid: spec.template: '
                "Invalid value: ...: field is immutable"
            ),
        })
        data = json.loads(result)
        assert data["status"] == "REJECTED_BY_MEDIC"   # accepted by the guard, handed off
        assert data["target_agent"] == "infra"


class TestConfigMapVerbatimEmbed:
    """The ConfigMap must embed the architect's verbatim dashboard JSON / Trino DDL,
    not an LLM re-typed copy that can introduce transcription errors (a stray ';')."""

    def test_retyped_dashboard_json_replaced_by_verbatim_source(self, tmp_path, monkeypatch):
        from agents.tools import _embed_source_files_into_configmap
        (tmp_path / "dashboards").mkdir()
        (tmp_path / "sql").mkdir()
        (tmp_path / "dashboards" / "monitoring_specs.json").write_text('{"uid": "x", "panels": []}')
        (tmp_path / "sql" / "setup_trino.sql").write_text("CREATE TABLE x (a INT);\n")
        monkeypatch.chdir(tmp_path)
        # The exact bug: a re-typed dashboard JSON with a stray ';' after the closing brace.
        bad = (
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: grafana-dash-config\n"
            'data:\n  monitoring_specs.json: |\n    {"uid": "x", "panels": []};\n'
        )
        out = _embed_source_files_into_configmap(bad)
        docs = list(yaml.safe_load_all(out))
        val = docs[0]["data"]["monitoring_specs.json"]
        json.loads(val)                 # parses now — stray ';' gone
        assert "};" not in val

    def test_noop_for_manifest_without_embed_keys(self, tmp_path, monkeypatch):
        from agents.tools import _embed_source_files_into_configmap
        monkeypatch.chdir(tmp_path)
        manifest = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: analytics\n"
        assert _embed_source_files_into_configmap(manifest) == manifest
