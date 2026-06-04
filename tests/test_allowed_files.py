"""Unit tests for the agent file-ownership guards.

Architect owns the data plane (scripts, SQL, dashboards, requirements); Infra owns
the deployment plane (Terraform, k8s, Dockerfile, CI). These guards stop a mis-routed
fix from letting one agent clobber the other's artifacts.
"""
from agents.architect import _normalize_filename, _is_architect_allowed_file
from agents.infra import _is_infra_allowed_file, files_exist_in_state


class TestNormalizeFilename:
    def test_backslashes_become_slashes(self):
        assert _normalize_filename("k8s\\job.yaml") == "k8s/job.yaml"

    def test_strips_whitespace(self):
        assert _normalize_filename("  scripts/p.py  ") == "scripts/p.py"

    def test_none_becomes_empty(self):
        assert _normalize_filename(None) == ""


class TestArchitectAllowedFile:
    def test_requirements_allowed(self):
        assert _is_architect_allowed_file("requirements.txt") is True

    def test_python_allowed(self):
        assert _is_architect_allowed_file("scripts/pipe.py") is True

    def test_sql_allowed(self):
        assert _is_architect_allowed_file("sql/setup_trino.sql") is True

    def test_dashboard_json_allowed(self):
        assert _is_architect_allowed_file("dashboards/monitoring_specs.json") is True

    def test_dockerfile_blocked(self):
        assert _is_architect_allowed_file("Dockerfile") is False

    def test_terraform_blocked(self):
        assert _is_architect_allowed_file("terraform/main.tf") is False

    def test_yaml_blocked(self):
        assert _is_architect_allowed_file("k8s/job.yaml") is False

    def test_empty_blocked(self):
        assert _is_architect_allowed_file("") is False


class TestInfraAllowedFile:
    def test_python_blocked(self):
        assert _is_infra_allowed_file("scripts/pipe.py") is False

    def test_sql_blocked(self):
        assert _is_infra_allowed_file("sql/setup_trino.sql") is False

    def test_requirements_blocked(self):
        assert _is_infra_allowed_file("requirements.txt") is False

    def test_dashboard_blocked(self):
        assert _is_infra_allowed_file("dashboards/monitoring_specs.json") is False

    def test_k8s_yaml_allowed(self):
        assert _is_infra_allowed_file("k8s/job.yaml") is True

    def test_dockerfile_allowed(self):
        assert _is_infra_allowed_file("Dockerfile") is True

    def test_terraform_allowed(self):
        assert _is_infra_allowed_file("terraform/main.tf") is True

    def test_empty_blocked(self):
        assert _is_infra_allowed_file("") is False


class TestOwnershipIsComplementaryForDataPlane:
    """A data-plane artifact is architect-allowed AND infra-blocked (never both own it)."""
    def test_python_script(self):
        assert _is_architect_allowed_file("scripts/p.py") is True
        assert _is_infra_allowed_file("scripts/p.py") is False

    def test_dashboard(self):
        assert _is_architect_allowed_file("dashboards/monitoring_specs.json") is True
        assert _is_infra_allowed_file("dashboards/monitoring_specs.json") is False


class TestFilesExistInState:
    def test_subset_present(self):
        assert files_exist_in_state(["k8s/job.yaml"], ["k8s/job.yaml", "Dockerfile"]) is True

    def test_case_insensitive(self):
        assert files_exist_in_state(["K8S/Job.yaml"], ["k8s/job.yaml"]) is True

    def test_missing_returns_false(self):
        assert files_exist_in_state(["k8s/job.yaml"], ["Dockerfile"]) is False

    def test_empty_target_returns_false(self):
        assert files_exist_in_state([], ["Dockerfile"]) is False
