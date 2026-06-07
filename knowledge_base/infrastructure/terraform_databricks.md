# STANDARD: TERRAFORM — DATABRICKS PIPELINE (Delta + Unity Catalog + Jobs)

This standard governs the **pipeline-level** Terraform for a Databricks pipeline
(`provider: databricks`). It is fundamentally different from the AWS/Azure/GCP object-storage
standards — there is **no S3/GCS/ADLS bucket, no IAM policy, no Kubernetes**.

## What the bootstrap already created (DO NOT recreate)
`bootstrap/databricks/` provisions the workspace, **jobs cluster**, SQL warehouse, Unity Catalog
metastore, catalog, the `raw` schema, and the host-cloud storage (S3 DBFS root). The pipeline
Terraform consumes these by ID/name — it never re-declares them.

## What the pipeline Terraform creates
Exactly five files in `/terraform`:

| File | Responsibility |
|---|---|
| `providers.tf` | `databricks` provider + remote backend on the host cloud (S3 for host_cloud=aws) |
| `main.tf` | `databricks_secret_scope` + `databricks_secret` (source DB creds) and `databricks_job` running the Spark task on the existing cluster |
| `variables.tf` | `catalog`, `schema`, and `databricks_client_id` (the SP app id for `run_as`, from `TF_VAR_databricks_client_id`). NO `db_*` (read from SSM via `data "aws_ssm_parameter"`), and NO `existing_cluster_id` (resolved by the `databricks_cluster` data source by name). |
| `outputs.tf` | `job_id`, `job_url` |
| `terraform.tfvars` | `catalog` + `schema` only. Do **NOT** put `databricks_client_id` here — it comes from `TF_VAR_databricks_client_id` (env); a tfvars value would override that env var. |

### Provider + backend (`providers.tf`)
```hcl
terraform {
  required_providers {
    databricks = { source = "databricks/databricks", version = "~> 1.0" }
    aws        = { source = "hashicorp/aws", version = "~> 5.0" }   # to read the DB creds from SSM
  }
  # host_cloud = aws → reuse the existing S3 state bucket
  backend "s3" {
    bucket = "<state_bucket>"
    key    = "<state_key>"            # e.g. terraform/sales-lakehouse/terraform.tfstate
    region = "<region>"
  }
}
# Databricks auth: the SERVICE PRINCIPAL via OAuth (DATABRICKS_HOST + DATABRICKS_CLIENT_ID +
# DATABRICKS_CLIENT_SECRET env). The pipeline runs as the SP so the job it creates runs as the SP
# (default run_as = creator) on the SP's SINGLE_USER jobs cluster — no servicePrincipal.user role
# binding is needed (a user PAT cannot bind the SP into run_as). auth_type is pinned to oauth-m2m
# so the provider ignores the ARM_*/GOOGLE_CREDENTIALS env the agent run also sets.
provider "databricks" {
  auth_type = "oauth-m2m"
}
# AWS auth: the runner's AWS creds (same as the S3 backend) — used ONLY to read SSM.
provider "aws" {
  region = "<region>"
}
```

### Secret scope + job (`main.tf`)
The source DB connection is published to **SSM by the bootstrap** (`bootstrap/databricks/ssm.tf`,
under `/multi-cloud-self-healing-agent/aws/lakehouse_db_*`) — never a GitHub secret, never plaintext.
This terraform reads it from SSM: the **password** into a Databricks secret scope (the Spark job
reads it via `dbutils.secrets.get`), and host/name/user become job parameters.
```hcl
resource "databricks_secret_scope" "pipeline" {
  name = "<pipeline_id>"
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
  cluster_name = "<workspace_name>-jobs-cluster"   # bootstrap names it "${workspace_name}-jobs-cluster"
}

resource "databricks_job" "pipeline" {
  name = "<pipeline_id>"

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

    # The cluster ships NO source-DB JDBC driver — attach it, or spark.read.format("jdbc")
    # fails ClassNotFoundException. Postgres source → postgresql; MySQL source → mysql-connector-j.
    library {
      maven { coordinates = "org.postgresql:postgresql:42.7.3" }
    }

    spark_python_task {
      python_file = "dbfs:/pipelines/<pipeline_id>/<script_name>.py"   # CI uploads the script here first
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

  # Daily schedule (matches update_frequency); the CI additionally `run-now`s once to verify.
  schedule {
    quartz_cron_expression = "0 0 6 * * ?"
    timezone_id            = "UTC"
  }
}
```

### Outputs (`outputs.tf`)
```hcl
output "job_id"  { value = databricks_job.pipeline.id }
output "job_url" { value = databricks_job.pipeline.url }
```

## Hard rules
- **NO** `aws_s3_bucket`, `aws_iam_policy`, `glue`, `azurerm_*`, `google_storage_*`, or any
  Kubernetes/Helm resource in the pipeline Terraform — those belong to the other clouds or to
  the Databricks bootstrap.
- The jobs cluster is **referenced by `existing_cluster_id`**, never created here.
- DB credentials go through `databricks_secret`, never plaintext.
- Unity Catalog `catalog`/`schema` come from the infra context — never invented.
