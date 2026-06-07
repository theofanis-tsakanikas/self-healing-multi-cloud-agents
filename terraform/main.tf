resource "databricks_secret_scope" "pipeline" {
  name = "pipe_sales_lakehouse"
}

data "aws_ssm_parameter" "db_host"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_host" }
data "aws_ssm_parameter" "db_name"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_name" }
data "aws_ssm_parameter" "db_user"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_user" }
data "aws_ssm_parameter" "db_password" { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_password" }

resource "databricks_secret" "db_password" {
  key          = "db_password"
  string_value = data.aws_ssm_parameter.db_password.value
  scope        = databricks_secret_scope.pipeline.name
}

data "databricks_cluster" "jobs" {
  cluster_name = "multi-cloud-agent-workspace-jobs-cluster"
}

resource "databricks_job" "pipeline" {
  name = "pipe_sales_lakehouse"

  task {
    task_key            = "etl"
    existing_cluster_id = data.databricks_cluster.jobs.id

    library {
      maven { coordinates = "org.postgresql:postgresql:42.7.3" }
    }

    spark_python_task {
      python_file = "dbfs:/pipelines/pipe_sales_lakehouse/pipe_sales_lakehouse.py"
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