resource "databricks_secret_scope" "pipeline" {
  
  name = "pipe_sales_lakehouse"
}

# DB connection from SSM (published by bootstrap/databricks/ssm.tf). One source of truth.
data "aws_ssm_parameter" "db_host"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_host" }
data "aws_ssm_parameter" "db_name"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_name" }
data "aws_ssm_parameter" "db_user"     { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_user" }
data "aws_ssm_parameter" "db_password" { name = "/multi-cloud-self-healing-agent/aws/lakehouse_db_password" }

# ONLY the password goes into the secret scope; host/name/user are non-sensitive job params.
resource "databricks_secret" "db_password" {
  key          = "db_password"
  string_value = data.aws_ssm_parameter.db_password.value
  scope        = databricks_secret_scope.pipeline.name
}

# Resolve the bootstrap jobs cluster BY NAME — no cluster id has to be wired through context.
data "databricks_cluster" "jobs" {
  cluster_name = "multi-cloud-agent-workspace-jobs-cluster"
}

resource "databricks_job" "pipeline" {
  name = "pipe_sales_lakehouse"

  # Run as the service principal — the SINGLE_USER the bootstrap assigned the jobs cluster to, so
  # the job has the SP's Unity Catalog access. The pipeline provider authenticates AS this SP
  # (oauth-m2m), so it can bind the SP into run_as (a user PAT cannot — "must have
  # servicePrincipal.user role on the SP"). var.databricks_client_id = the SP application id.
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

# Observability — Lakeview (AI/BI) dashboard.
data "databricks_sql_warehouse" "obs" {
  name = "multi-cloud-agent-workspace-warehouse"
}

resource "databricks_dashboard" "observability" {
  display_name      = "pipe_sales_lakehouse — Observability"
  parent_path       = "/Shared"
  warehouse_id      = data.databricks_sql_warehouse.obs.id
  file_path         = "${path.module}/../dashboards/pipe_sales_lakehouse_lakeview.json"
  embed_credentials = false
}