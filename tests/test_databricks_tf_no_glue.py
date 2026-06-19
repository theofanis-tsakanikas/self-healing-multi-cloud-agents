"""
Regression (2026-06-19): the Databricks pipeline terraform uses AWS data sources
(`data "aws_ssm_parameter"` for the lakehouse DB) so CLOUD_PROVIDER reads "aws" — which made
validate_generated_code fire the AWS "Glue permissions missing / Trino uses Glue Data Catalog"
IAM check on a Databricks main.tf. But Databricks is Spark + Delta + Unity Catalog — NO Trino,
NO Glue. The check must SKIP a databricks terraform (detected by its databricks_* resources),
while STILL firing on a real AWS object-storage terraform.
"""
import os
import tempfile

os.environ["CLOUD_PROVIDER"] = "aws"           # makes the Glue check relevant
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from agents.tools import validate_generated_code

_GLUE_MARK = "AWS Glue permissions missing"

_DATABRICKS_TF = '''resource "databricks_secret_scope" "pipeline" { name = "x" }
data "aws_ssm_parameter" "db_host" { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_host" }
resource "databricks_secret" "db_password" { key = "db_password" scope = databricks_secret_scope.pipeline.name string_value = "x" }
data "databricks_cluster" "jobs" { cluster_name = "c" }
resource "databricks_job" "pipeline" { name = "x" }
resource "databricks_dashboard" "o" { display_name = "x" }'''

_AWS_TF_NO_GLUE = '''resource "aws_s3_bucket" "data" { bucket = "x" }
resource "aws_iam_role_policy" "p" { policy = "s3:GetObject" }'''

_AWS_TF_WITH_GLUE = '''resource "aws_s3_bucket" "data" { bucket = "x" }
resource "aws_iam_role_policy" "p" { policy = "glue:GetTable, glue:CreateTable" }'''


def _validate_main_tf(content: str) -> str:
    d = tempfile.mkdtemp()
    f = os.path.join(d, "main.tf")
    with open(f, "w") as fh:
        fh.write(content)
    return str(validate_generated_code.invoke({"filename": f}))


def test_databricks_tf_does_not_flag_glue():
    assert _GLUE_MARK not in _validate_main_tf(_DATABRICKS_TF)


def test_aws_tf_without_glue_still_flags():
    assert _GLUE_MARK in _validate_main_tf(_AWS_TF_NO_GLUE)


def test_aws_tf_with_glue_is_clean():
    assert _GLUE_MARK not in _validate_main_tf(_AWS_TF_WITH_GLUE)
