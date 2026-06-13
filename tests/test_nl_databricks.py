"""
NL-authored Databricks deploy: cloud_override="databricks" builds a Spark/Delta/Unity-Catalog
bundle (mirrors the validated sales_lakehouse YAML), NOT the object-storage K8s/Trino/Grafana
bundle. The business answers are cloud-agnostic and reused as-is.
"""
from unittest.mock import patch

import utils.nlp_parser as nlp


_ANSWERS = {
    "pipeline_slug": "retail_sales",
    "data_domain": "sales",
    "source_db_type": "postgres",
    "source_table": "raw_retail_sales",
    "target_cloud": "aws",  # primary wizard cloud; Databricks is the end-of-flow override
    "frequency": "daily",
    "owner_team": "analytics_team",
}

_DB_OUTPUTS = {"databricks": {
    "catalog_name": "multi_cloud_agent_workspace",
    "warehouse_id": "abc123",
    "source_db_endpoint": "lakehouse.xxxx.eu-central-1.rds.amazonaws.com",
}}


def _bundle(outputs):
    with patch.object(nlp, "_load_bootstrap_outputs", return_value=outputs), \
         patch.object(nlp, "_save_generated_configs"):
        return nlp._build_from_answers(_ANSWERS, [], cloud_override="databricks")


def test_databricks_bundle_is_lakehouse_not_object_storage():
    pc, dc, rc, ic, pid, task = _bundle(_DB_OUTPUTS)

    assert pid == "pipe_retail_sales_lakehouse"
    assert pc["target_infra_config"] == "configs/infra/databricks.yaml"
    assert pc["cloud_provider"] == "aws"  # host cloud
    assert pc["databricks_target"]["catalog"] == "multi_cloud_agent_workspace"
    assert pc["databricks_target"]["table_name"] == "pipe_retail_sales_lakehouse"

    # Databricks artifact set — no K8s/Trino/Grafana/requirements
    assert "shared_services" not in pc
    assert pc["required_artifacts"] == [
        "scripts/pipe_retail_sales_lakehouse.py",
        "sql/setup_unity_catalog.sql",
        "dashboards/pipe_retail_sales_lakehouse_lakeview.json",
    ]

    # infra config signals the Databricks execution model to the agent
    assert ic.get("provider") == "databricks"

    # source = lakehouse postgres, the user's table reused verbatim
    assert dc["db_type"] == "postgres"
    assert dc["default_table"] == "raw_retail_sales"

    # task is the PySpark/Delta/Unity-Catalog brief
    assert "PYSPARK" in task
    assert "Unity Catalog" in task
    assert "Delta" in task


def test_databricks_catalog_falls_back_without_bootstrap_output():
    pc, *_ = _bundle({"databricks": {}})
    assert pc["databricks_target"]["catalog"] == "multi_cloud_agent_workspace"
