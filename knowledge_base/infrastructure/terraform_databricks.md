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
| `variables.tf` | `catalog`, `schema`, `db_host`/`db_name`/`db_user` (non-sensitive, from CI `TF_VAR_db_*`), `db_password` (sensitive). NO `existing_cluster_id` — the cluster is resolved by the `databricks_cluster` data source (by name). |
| `outputs.tf` | `job_id`, `job_url` |
| `terraform.tfvars` | Concrete values from the infra context |

### Provider + backend (`providers.tf`)
```hcl
terraform {
  required_providers {
    databricks = { source = "databricks/databricks", version = "~> 1.0" }
  }
  # host_cloud = aws → reuse the existing S3 state bucket
  backend "s3" {
    bucket = "<state_bucket>"
    key    = "<state_key>"            # e.g. terraform/eu-sales-delta/terraform.tfstate
    region = "<region>"
  }
}
# Auth comes from DATABRICKS_HOST + DATABRICKS_TOKEN env vars (never hardcode).
provider "databricks" {}
```

### Secret scope + job (`main.tf`)
The source DB password is stored in a **Databricks secret scope** — the Spark job reads it via
`dbutils.secrets.get(...)`. NEVER pass credentials as plaintext job parameters.
```hcl
resource "databricks_secret_scope" "pipeline" {
  name = "<pipeline_id>"
}

# ONLY the password is sensitive. host/name/user travel as job parameters (below).
resource "databricks_secret" "db_password" {
  key          = "db_password"
  string_value = var.db_password           # from TF_VAR_db_password (a GitHub secret)
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
        "--db-host", var.db_host,
        "--db-name", var.db_name,
        "--db-user", var.db_user,
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
