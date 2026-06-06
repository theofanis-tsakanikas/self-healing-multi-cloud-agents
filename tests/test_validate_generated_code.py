"""Unit tests for tools.validate_generated_code — the safety-net validator.

A validator that never fails is useless, so every test proves a rule actually
FIRES on a crafted BAD fixture, and (where it could false-positive) stays SILENT
on a GOOD one. Fixtures are written to tmp_path — nothing leaks into the repo and
no generated artifact is touched.
"""
from agents.tools import validate_generated_code


def _validate(tmp_path, name, content):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return validate_generated_code.invoke({"filename": str(f)})


# ── Python: credential policy ───────────────────────────────────────────────────
class TestPythonCredentialPolicy:
    def test_os_getenv_for_db_cred_is_policy_violation(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        'import os\nhost = os.getenv("POSTGRES_DB_HOST")\n')
        assert "VALIDATION FAILED" in out
        assert "POLICY VIOLATION" in out

    def test_cloud_get_does_not_trigger_policy_violation(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        'from utils.cloud_config import cloud_get\n'
                        'if _CLOUD == "aws":\n'
                        '    host = cloud_get("aws", "db_host", db_type="postgres")\n')
        assert "POLICY VIOLATION" not in out


# ── Python: storage_options / destination_uri ───────────────────────────────────
class TestPythonStorageChecks:
    def test_double_brace_storage_options_flagged(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        'import pandas as pd\n'
                        'df = pd.DataFrame()\n'
                        'df.to_parquet("x", storage_options={{}})\n')
        assert "STORAGE" in out and "double braces" in out

    def test_single_brace_storage_options_not_flagged_for_double(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        'import pandas as pd\n'
                        'df = pd.DataFrame()\n'
                        'df.to_parquet("x", storage_options={})\n')
        assert "double braces" not in out

    def test_hardcoded_destination_uri_flagged(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        'destination_uri = "s3://my-bucket/processed/"\n')
        assert "hardcoded" in out


# ── Python: cloud guard + business rules ────────────────────────────────────────
class TestPythonCloudGuardAndRules:
    def test_unguarded_cloud_get_flagged(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        'from utils.cloud_config import cloud_get\n'
                        'host = cloud_get("aws", "db_host", db_type="postgres")\n')
        assert "CLOUD GUARD" in out

    def test_is_suspicious_false_placeholder_flagged(self, tmp_path):
        out = _validate(tmp_path, "p.py", "chunk['is_suspicious'] = False\n")
        assert "BUSINESS RULES" in out


# ── JSON: Grafana dashboard (incl. the $project_id template-var rule) ────────────
_GOOD_DASHBOARD = """
{
  "uid": "x-observability",
  "title": "X Observability",
  "schemaVersion": 37,
  "templating": {"list": [
    {"name": "project_id", "type": "query",
     "query": "label_values(pipeline_rows_processed_total, project_id)"}
  ]},
  "panels": [
    {"id": 1, "title": "Record Count", "type": "stat",
     "targets": [{"expr": "pipeline_rows_processed_total{project_id=~\\"$project_id\\"}"}]}
  ]
}
"""


class TestGrafanaDashboard:
    def test_good_dashboard_is_clean(self, tmp_path):
        out = _validate(tmp_path, "monitoring_specs.json", _GOOD_DASHBOARD)
        assert out.startswith("CLEAN")

    def test_hardcoded_project_id_flagged(self, tmp_path):
        bad = """
        {"uid": "x", "title": "x", "schemaVersion": 37,
         "templating": {"list": []},
         "panels": [{"targets": [{"expr": "pipeline_rows_processed_total{project_id=\\"unknown\\"}"}]}]}
        """
        out = _validate(tmp_path, "monitoring_specs.json", bad)
        assert "hardcodes project_id" in out

    def test_missing_project_id_template_var_flagged(self, tmp_path):
        bad = """
        {"uid": "x", "title": "x", "schemaVersion": 37,
         "templating": {"list": []},
         "panels": [{"targets": [{"expr": "pipeline_rows_processed_total{project_id=~\\"$project_id\\"}"}]}]}
        """
        out = _validate(tmp_path, "monitoring_specs.json", bad)
        assert "missing the $project_id template variable" in out

    def test_missing_mandatory_fields_flagged(self, tmp_path):
        out = _validate(tmp_path, "monitoring_specs.json",
                        '{"panels": [{"targets": [{"expr": "x"}]}]}')
        assert "missing mandatory fields" in out

    def test_empty_panels_flagged(self, tmp_path):
        out = _validate(tmp_path, "monitoring_specs.json",
                        '{"uid": "x", "title": "x", "schemaVersion": 37, "panels": []}')
        assert "non-empty list" in out

    def test_invalid_json_flagged(self, tmp_path):
        out = _validate(tmp_path, "monitoring_specs.json", "{not valid json")
        assert "JSON SYNTAX ERROR" in out


class TestK8sImageTagPolicy:
    _PUBLIC_ERROR = "found for public images"

    def _manifest(self, image):
        return (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            f"      - name: c\n        image: {image}\n"
        )

    def test_public_image_latest_is_flagged(self, tmp_path):
        out = _validate(tmp_path, "dep.yaml", self._manifest("redis:latest"))
        assert self._PUBLIC_ERROR in out

    def test_gcp_artifact_registry_latest_is_exempt(self, tmp_path):
        # Private app image whose tag CI rewrites to the commit SHA → acceptable.
        out = _validate(tmp_path, "dep.yaml",
                        self._manifest("europe-west3-docker.pkg.dev/proj/repo:latest"))
        assert self._PUBLIC_ERROR not in out

    def test_azure_acr_latest_is_exempt(self, tmp_path):
        out = _validate(tmp_path, "dep.yaml",
                        self._manifest("myreg.azurecr.io/app/repo:latest"))
        assert self._PUBLIC_ERROR not in out

    def test_aws_ecr_latest_is_exempt(self, tmp_path):
        out = _validate(tmp_path, "dep.yaml",
                        self._manifest("123.dkr.ecr.eu-central-1.amazonaws.com/repo:latest"))
        assert self._PUBLIC_ERROR not in out


class TestConfigMapEmbeddedJSON:
    _BASE = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        "  name: grafana-dash-config\n"
        "data:\n"
        "  monitoring_specs.json: |\n"
        "    {body}\n"
    )

    def test_valid_embedded_json_is_clean(self, tmp_path):
        out = _validate(tmp_path, "configmaps.yaml",
                        self._BASE.format(body='{"uid": "x", "panels": []}'))
        assert "is not valid JSON" not in out

    def test_stray_semicolon_in_embedded_json_flagged(self, tmp_path):
        # The exact infra-transcription bug: a ';' after the root closing brace.
        out = _validate(tmp_path, "configmaps.yaml",
                        self._BASE.format(body='{"uid": "x", "panels": []};'))
        assert "is not valid JSON" in out


class TestIsSuspiciousCrossFile:
    _DDL_NO_FLAG = (
        "CREATE TABLE hive.s.pipe_x (\n"
        "  campaign_id VARCHAR,\n"
        "  run_date DATE\n"
        ") WITH (format='PARQUET', external_location='gs://b/processed/', "
        "partitioned_by=ARRAY['run_date']);\n"
    )
    _DDL_WITH_FLAG = (
        "CREATE TABLE hive.s.pipe_x (\n"
        "  campaign_id VARCHAR,\n"
        "  is_suspicious BOOLEAN,\n"
        "  run_date DATE\n"
        ") WITH (format='PARQUET', external_location='gs://b/processed/', "
        "partitioned_by=ARRAY['run_date']);\n"
    )
    _PY_FLAG = "chunk['is_suspicious'] = chunk['clicks'] > chunk['impressions']\n"
    _PY_NO_FLAG = "chunk = chunk.dropna(subset=['campaign_id'])\n"

    def _validate_sql(self, tmp_path, ddl, py):
        (tmp_path / "sql").mkdir()
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "pipe_x.py").write_text(py)
        sql_f = tmp_path / "sql" / "setup_trino.sql"
        sql_f.write_text(ddl)
        return validate_generated_code.invoke({"filename": str(sql_f)})

    def test_script_flags_but_ddl_omits_is_flagged(self, tmp_path):
        out = self._validate_sql(tmp_path, self._DDL_NO_FLAG, self._PY_FLAG)
        assert "consistency" in out.lower() and "is_suspicious" in out

    def test_both_have_is_suspicious_is_clean(self, tmp_path):
        out = self._validate_sql(tmp_path, self._DDL_WITH_FLAG, self._PY_FLAG)
        assert "consistency" not in out.lower()

    def test_neither_has_is_suspicious_is_clean(self, tmp_path):
        out = self._validate_sql(tmp_path, self._DDL_NO_FLAG, self._PY_NO_FLAG)
        assert "consistency" not in out.lower()

    def test_ddl_has_but_script_omits_is_flagged(self, tmp_path):
        out = self._validate_sql(tmp_path, self._DDL_WITH_FLAG, self._PY_NO_FLAG)
        assert "consistency" in out.lower()


class TestTerraformGcpProcessedDir:
    _BUCKET = 'resource "google_storage_bucket" "data" {\n  name = "b"\n}\n'

    def test_gcp_missing_processed_dir_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        out = _validate(tmp_path, "main.tf", self._BUCKET)
        assert "processed/" in out and "directory" in out.lower()

    def test_gcp_with_processed_dir_clean(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        tf = self._BUCKET + (
            'resource "google_storage_bucket_object" "processed_dir" {\n'
            '  name = "processed/"\n  bucket = google_storage_bucket.data.name\n  content = " "\n}\n'
        )
        out = _validate(tmp_path, "main.tf", tf)
        assert "pre-created processed" not in out

    def test_aws_main_tf_not_flagged_for_processed_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        out = _validate(tmp_path, "main.tf", 'resource "aws_s3_bucket" "data" {}\n')
        assert "pre-created processed" not in out
