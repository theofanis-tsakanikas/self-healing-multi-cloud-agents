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
| `variables.tf` | `catalog`, `schema` only. NO `db_*` (the connection is read from SSM via `data "aws_ssm_parameter"`), and NO `existing_cluster_id` (resolved by the `databricks_cluster` data source by name). |
| `outputs.tf` | `job_id`, `job_url` |
| `terraform.tfvars` | Concrete values from the infra context |

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
# Databricks auth: DATABRICKS_HOST + DATABRICKS_TOKEN env vars (never hardcode). auth_type is
# pinned to "pat" so the provider ignores the ARM_*/GOOGLE_CREDENTIALS env the agent run also sets
# (otherwise: "more than one authorization method configured: azure and google and oauth").
provider "databricks" {
  auth_type = "pat"
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
