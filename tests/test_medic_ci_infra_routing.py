"""
Regression for the Databricks infra-heal (2026-06-19): a CI-LOG runtime failure with a
missing-dependency signature (ClassNotFoundException — the JDBC driver library is not attached to
the databricks_job) must route the medic's fix to the INFRA agent (the Terraform `library` block),
NOT the architect — even though the exception surfaces at the Spark script's read line.

CRITICAL — protect the validated AWS/Azure/GCP runs (ZERO mis-routing):
  - a normal pandas/script CI traceback (KeyError, ValueError, AnalysisException) must NOT match the
    infra signatures → it keeps the existing script-frame routing to the architect.
The signatures are JVM/Databricks-only strings, so they cannot appear in an object-storage clouds'
pandas traceback.
"""
import os

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from agents.medic import _ci_error_owner, _databricks_secret_key_exact_fix


class _Msg:
    def __init__(self, content):
        self.content = content


def _owner(text: str) -> str:
    return _ci_error_owner([_Msg(text)])


_SECRET_ERR = ("py4j...IllegalArgumentException: Secret does not exist with scope: "
               "pipe_sales_lakehouse and key: db_password")

_MAIN_TF = '''resource "databricks_secret_scope" "pipeline" {
  name = "pipe_sales_lakehouse"
}
resource "databricks_secret" "db_password" {
  key          = "postgres_password"
  string_value = data.aws_ssm_parameter.db_password.value
  scope        = databricks_secret_scope.pipeline.name
}
'''


def test_secret_key_exact_fix_targets_the_key_line(tmp_path, monkeypatch):
    """The Medic computes the EXACT old/new so the infra agent copies it instead of guessing
    (it kept patching the scope/name and missing the actual `key = "postgres_password"` line)."""
    import agents.medic as medic
    (tmp_path / "terraform").mkdir()
    (tmp_path / "terraform" / "main.tf").write_text(_MAIN_TF)
    monkeypatch.setattr(medic, "REPO_ROOT", tmp_path)

    res = _databricks_secret_key_exact_fix([_Msg(_SECRET_ERR)])
    assert res is not None
    old, new = res
    assert old.strip() == 'key          = "postgres_password"'   # the KEY line, verbatim
    assert new.strip() == 'key          = "db_password"'         # only the value swapped
    # it must NOT have touched the scope/name lines
    assert "scope" not in old and "name" not in old


def test_secret_key_exact_fix_none_when_already_correct(tmp_path, monkeypatch):
    import agents.medic as medic
    (tmp_path / "terraform").mkdir()
    (tmp_path / "terraform" / "main.tf").write_text(_MAIN_TF.replace("postgres_password", "db_password"))
    monkeypatch.setattr(medic, "REPO_ROOT", tmp_path)
    assert _databricks_secret_key_exact_fix([_Msg(_SECRET_ERR)]) is None


def test_secret_key_exact_fix_none_without_secret_error():
    assert _databricks_secret_key_exact_fix([_Msg("KeyError: 'campaign'")]) is None


# --- must route to INFRA (missing dependency / library) ---
def test_classnotfound_jdbc_driver_routes_infra():
    log = (
        'py4j.protocol.Py4JJavaError: An error occurred while calling o123.load.\n'
        ': java.lang.ClassNotFoundException: org.postgresql.Driver\n'
        '  File "/databricks/.../scripts/pipe_sales_lakehouse.py", line 48, in run\n'
        '    df = spark.read.format("jdbc")...'
    )
    assert _owner(log) == "infra"


def test_noclassdeffound_routes_infra():
    assert _owner("Caused by: java.lang.NoClassDefFoundError: org/postgresql/Driver") == "infra"


def test_library_install_failure_routes_infra():
    assert _owner("Library installation failed for library due to user error") == "infra"


def test_secret_not_found_routes_infra():
    # databricks_secret key ≠ dbutils.secrets.get key → fix the Terraform secret, not the script.
    log = (
        'py4j.protocol.Py4JJavaError: An error occurred while calling o45.get.\n'
        ': java.lang.IllegalArgumentException: Secret does not exist with scope: '
        'pipe_sales and key: db_password\n'
        '  File "/databricks/.../scripts/pipe_sales_lakehouse.py", line 85, in run'
    )
    assert _owner(log) == "infra"


def test_resource_does_not_exist_routes_infra():
    assert _owner("RESOURCE_DOES_NOT_EXIST: No secret scope found") == "infra"


# --- must NOT match (keep existing architect/script routing) — the 4-cloud safety net ---
def test_pandas_keyerror_not_infra():
    log = (
        'Traceback (most recent call last):\n'
        '  File "/app/scripts/pipe_etl.py", line 92, in run\n'
        "    chunk['campaign'] = chunk['campaign'].where(...)\n"
        "KeyError: 'campaign'"
    )
    assert _owner(log) == ""


def test_pandas_valueerror_not_infra():
    assert _owner("ValueError: could not convert string to float: 'not_a_number'") == ""


def test_spark_analysis_cannot_resolve_not_infra():
    # A genuine SCRIPT bug in Spark (wrong column) — must stay architect, not infra.
    assert _owner("AnalysisException: [UNRESOLVED_COLUMN] cannot resolve 'campaign'") == ""


def test_empty_messages_not_infra():
    assert _ci_error_owner([]) == ""
