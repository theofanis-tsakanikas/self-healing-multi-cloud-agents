resource "databricks_secret_scope" "pipeline" {
  name = "pipe_sales_dbx_pipeline_etl_lakehouse"
}

data "aws_ssm_parameter" "db_host"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_host" }
data "aws_ssm_parameter" "db_name"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_name" }
data "aws_ssm_parameter" "db_user"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_user" }
data "aws_ssm_parameter" "db_password" { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_password" }

resource "databricks_secret" "db_password" {
  key          = "postgres_password"
  string_value = data.aws_ssm_parameter.db_password.value
  scope        = "pipe_sales_dbx_pipeline_etl_lakehouse"
}

data "databricks_cluster" "jobs" {
  cluster_name = "multi-cloud-agent-workspace-jobs-cluster"
}

resource "databricks_job" "pipeline" {
  name = "pipe_sales_dbx_pipeline_etl_lakehouse"

  run_as {
    service_principal_name = var.databricks_client_id
  }

  task {
    task_key            = "etl"
    existing_cluster_id = data.databricks_cluster.jobs.id

    library {
      maven { coordinates = "org.postgresql:postgresql:42.7.3" }
    }

    spark_python_task {
      python_file = "dbfs:/pipelines/pipe_sales_dbx_pipeline_etl_lakehouse/pipe_sales_dbx_pipeline_etl_lakehouse.py"
      parameters = [
        "--catalog", var.catalog,
        "--schema", var.schema,
        "--secret-scope", databricks_secret_scope.pipeline.name,
        "--db-host", data.aws_ssm_parameter.db_host.value,
        "--db-name", data.aws_ssm_parameter.db_name.value,
        "--db-user", data.aws_ssm_parameter.db_user.value,
      ]
    }
  }

  schedule {
    quartz_cron_expression = "0 0 6 * * ?"
    timezone_id            = "UTC"
  }
}

data "databricks_sql_warehouse" "obs" {
  name = "multi-cloud-agent-workspace-warehouse"
}

resource "databricks_dashboard" "observability" {
  display_name      = "pipe_sales_dbx_pipeline_etl_lakehouse — Observability"
  parent_path       = "/Shared"
  warehouse_id      = data.databricks_sql_warehouse.obs.id
  file_path         = "${path.module}/../dashboards/pipe_sales_dbx_pipeline_etl_lakehouse_lakeview.json"
  embed_credentials = false
}