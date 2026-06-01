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
| `variables.tf` | `databricks_host`, `existing_cluster_id`, `catalog`, `schema`, `notebook_path`/`spark_python_task`, `db_password` (sensitive) |
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

resource "databricks_secret" "db_password" {
  key          = "db_password"
  string_value = var.db_password           # from TF_VAR_db_password (a GitHub secret)
  scope        = databricks_secret_scope.pipeline.name
}

resource "databricks_job" "pipeline" {
  name = "<pipeline_id>"

  task {
    task_key            = "etl"
    existing_cluster_id = var.existing_cluster_id      # the bootstrap jobs cluster

    spark_python_task {
      python_file = "dbfs:/pipelines/<pipeline_id>/<script_name>.py"
      # OR notebook_task pointing at a workspace-imported notebook.
      parameters  = [
        "--catalog", var.catalog,
        "--schema",  var.schema,
        "--secret-scope", databricks_secret_scope.pipeline.name,
      ]
    }
  }

  # Daily schedule (matches update_frequency); quartz cron in UTC.
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
