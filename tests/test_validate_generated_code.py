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

    def test_astype_float_in_business_rule_flagged(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        "chunk = chunk[chunk['ad_spend'].astype(float) >= 0.0]\n")
        assert "BUSINESS RULES" in out and "to_numeric" in out

    def test_to_numeric_coerce_is_clean(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        "chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce')\n"
                        "chunk = chunk[chunk['ad_spend'] >= 0.0]\n")
        assert "could not convert" not in out

    def test_int64_cast_not_flagged_as_float(self, tmp_path):
        out = _validate(tmp_path, "p.py", "chunk[col] = chunk[col].astype('Int64')\n")
        assert "astype(float)" not in out

    def test_temporal_compare_without_to_datetime_flagged(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        "_future = chunk['order_date'] > pd.Timestamp.now()\n")
        assert "BUSINESS RULES" in out and "Timestamp" in out

    def test_temporal_compare_with_to_datetime_is_clean(self, tmp_path):
        out = _validate(tmp_path, "p.py",
                        "chunk['order_date'] = pd.to_datetime(chunk['order_date'], errors='coerce')\n"
                        "_future = chunk['order_date'] > pd.Timestamp.now()\n")
        assert "Invalid comparison" not in out


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


class TestSqlDuplicateColumns:
    """The LLM sometimes prepends a phantom placeholder header above the real columns,
    producing duplicate names. Glue accepts it but Trino fails at runtime with a misleading
    'Table not found' on sync_partition_metadata — the validator must catch it pre-push."""

    def _validate(self, tmp_path, ddl):
        (tmp_path / "sql").mkdir()
        sql_f = tmp_path / "sql" / "setup_trino.sql"
        sql_f.write_text(ddl)
        return validate_generated_code.invoke({"filename": str(sql_f)})

    def test_phantom_header_with_duplicates_is_flagged(self, tmp_path):
        ddl = (
            "CREATE SCHEMA IF NOT EXISTS hive.s;\n"
            "DROP TABLE IF EXISTS hive.s.pipe_x;\n"
            "CREATE TABLE hive.s.pipe_x (\n"
            "  id INT,\n  data STRING,\n  run_date TIMESTAMP,\n  is_suspicious BOOLEAN,\n"
            "  order_id VARCHAR,\n  unit_price DECIMAL(18,2),\n"
            "  is_suspicious BOOLEAN,\n  run_date DATE\n"
            ") WITH (format='PARQUET', external_location='s3://b/processed/', "
            "partitioned_by=ARRAY['run_date']);\n"
        )
        out = self._validate(tmp_path, ddl)
        assert "duplicate column" in out.lower()
        assert "run_date" in out and "is_suspicious" in out

    def test_clean_ddl_has_no_duplicate_error(self, tmp_path):
        ddl = (
            "CREATE TABLE hive.s.pipe_x (\n"
            "  order_id VARCHAR,\n  unit_price DECIMAL(18,2),\n"
            "  is_suspicious BOOLEAN,\n  run_date DATE\n"
            ") WITH (format='PARQUET', external_location='s3://b/processed/', "
            "partitioned_by=ARRAY['run_date']);\n"
        )
        out = self._validate(tmp_path, ddl)
        assert "duplicate column" not in out.lower()


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

    def test_aws_missing_processed_dir_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        monkeypatch.delenv("PIPELINE_PLATFORM", raising=False)
        out = _validate(tmp_path, "main.tf", 'resource "aws_s3_bucket" "data_bucket" {}\n')
        assert "pre-created processed" in out and "aws_s3_object" in out

    def test_aws_with_processed_dir_clean(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        monkeypatch.delenv("PIPELINE_PLATFORM", raising=False)
        tf = (
            'resource "aws_s3_bucket" "data_bucket" {}\n'
            'resource "aws_s3_object" "processed_dir" {\n'
            '  bucket = aws_s3_bucket.data_bucket.id\n  key = "processed/"\n  content = " "\n}\n'
        )
        out = _validate(tmp_path, "main.tf", tf)
        assert "pre-created processed" not in out

    def test_aws_databricks_host_not_flagged_for_processed_dir(self, tmp_path, monkeypatch):
        # Databricks host_cloud=aws but uses Unity Catalog (no Trino/S3 external table) → no marker.
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        monkeypatch.setenv("PIPELINE_PLATFORM", "databricks")
        out = _validate(tmp_path, "main.tf", 'resource "databricks_job" "x" {}\n')
        assert "pre-created processed" not in out


class TestGhaImageTagSed:
    def _wf(self, tmp_path, sed_line):
        d = tmp_path / ".github" / "workflows"
        d.mkdir(parents=True)
        f = d / "x_pipeline.yml"
        f.write_text(
            "name: Deploy\non:\n  push:\n    paths: ['k8s/**']\n"
            "jobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - name: Set Image Tag\n        run: |\n          " + sed_line + "\n"
        )
        return validate_generated_code.invoke({"filename": str(f)})

    def test_mangled_sed_sha_in_name_flagged(self, tmp_path):
        # The exact bug: SHA appended to the image NAME with '-' (no colon) → tag dropped.
        out = self._wf(
            tmp_path,
            "sed -i 's|image: host/repo/img-.*|image: host/repo/img-${{ github.sha }}|' k8s/job.yaml",
        )
        assert "image NAME" in out and "ImagePullBackOff" in out

    def test_correct_tag_sed_is_clean(self, tmp_path):
        out = self._wf(
            tmp_path,
            "sed -i 's|image: host/repo/img:.*|image: host/repo/img:${{ github.sha }}|' k8s/job.yaml",
        )
        assert "image NAME" not in out


class TestGcpJobImageNoGhaExpr:
    def _job(self, image):
        return (
            "apiVersion: batch/v1\nkind: Job\nmetadata:\n  name: x\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            f"      - name: pipeline\n        image: {image}\n"
        )

    def test_gha_expr_in_gcp_image_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        out = _validate(tmp_path, "job.yaml",
                        self._job("europe-west3-docker.pkg.dev/p/r/img:${{ github.sha }}"))
        assert "InvalidImageName" in out

    def test_latest_gcp_image_clean(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        out = _validate(tmp_path, "job.yaml",
                        self._job("europe-west3-docker.pkg.dev/p/r/img:latest"))
        assert "InvalidImageName" not in out
