# ---------------------------------------------------------------------------
# S3 bucket — DBFS root storage for the Databricks workspace
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "dbfs_root" {
  bucket        = var.bucket_name
  force_destroy = true # ephemeral demo — let destroy.yml tear it down even with DBFS data

  tags = {
    Name        = var.bucket_name
    Project     = "multi-cloud-agent"
    ManagedBy   = "terraform-bootstrap"
    Environment = "bootstrap"
  }
}

resource "aws_s3_bucket_versioning" "dbfs_root" {
  bucket = aws_s3_bucket.dbfs_root.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dbfs_root" {
  bucket = aws_s3_bucket.dbfs_root.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "dbfs_root" {
  bucket                  = aws_s3_bucket.dbfs_root.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# DBFS-root bucket policy — REQUIRED for the MWS storage-configuration validation. Databricks
# validates List/Put/PutWithBucketOwnerFullControl/Delete on the bucket; without this policy the
# workspace create fails "Failed storage configuration validation checks ... Access Denied". The
# official data source generates exactly the grant Databricks needs (principal 414351767826).
# Grants a specific AWS account (not public) → allowed despite block_public_policy.
data "databricks_aws_bucket_policy" "dbfs_root" {
  provider = databricks.accounts
  bucket   = aws_s3_bucket.dbfs_root.bucket
}

resource "aws_s3_bucket_policy" "dbfs_root" {
  bucket     = aws_s3_bucket.dbfs_root.id
  policy     = data.databricks_aws_bucket_policy.dbfs_root.json
  depends_on = [aws_s3_bucket_public_access_block.dbfs_root]
}

# ---------------------------------------------------------------------------
# IAM cross-account role — allows Databricks control plane to manage AWS
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "databricks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::414351767826:root"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "cross_account" {
  name               = "${var.workspace_name}-cross-account-role"
  assume_role_policy = data.aws_iam_policy_document.databricks_assume.json

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

data "aws_iam_policy_document" "cross_account_policy" {
  statement {
    sid    = "DatabricksEC2"
    effect = "Allow"
    actions = [
      "ec2:*",
      "iam:PassRole",
      "iam:CreateServiceLinkedRole",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DatabricksS3"
    effect = "Allow"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
      "s3:ListBucket", "s3:GetBucketLocation",
    ]
    resources = [
      aws_s3_bucket.dbfs_root.arn,
      "${aws_s3_bucket.dbfs_root.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "cross_account" {
  name   = "databricks-cross-account-policy"
  role   = aws_iam_role.cross_account.id
  policy = data.aws_iam_policy_document.cross_account_policy.json
}

# IAM/S3 are eventually consistent — Databricks validates the cross-account role AND the bucket
# policy the instant the MWS resources are created, but the just-created role/policy/bucket-policy
# may not have propagated yet → "Failed credential/storage validation checks". Gate the MWS
# resources on this delay so both have settled. (mws_credentials and mws_workspaces both run after.)
resource "time_sleep" "wait_for_iam" {
  depends_on = [
    aws_iam_role.cross_account,
    aws_iam_role_policy.cross_account,
    aws_s3_bucket_policy.dbfs_root,
  ]
  create_duration = "30s"

  # Re-arm the wait whenever the role or bucket policy is (re)created — so a re-run that adds
  # the bucket policy to an already-applied state still waits 30s before the MWS validation,
  # instead of relying on the prior (already-elapsed) sleep.
  triggers = {
    role_arn      = aws_iam_role.cross_account.arn
    bucket_policy = aws_s3_bucket_policy.dbfs_root.id
  }
}

# ---------------------------------------------------------------------------
# Databricks workspace (MWS — multi-workspace / E2 deployment model)
# ---------------------------------------------------------------------------
resource "databricks_mws_credentials" "this" {
  provider         = databricks.accounts
  account_id       = var.account_id
  credentials_name = "${var.workspace_name}-credentials"
  role_arn         = aws_iam_role.cross_account.arn
  depends_on       = [time_sleep.wait_for_iam]
}

resource "databricks_mws_storage_configurations" "this" {
  provider                   = databricks.accounts
  account_id                 = var.account_id
  storage_configuration_name = "${var.workspace_name}-storage"
  bucket_name                = aws_s3_bucket.dbfs_root.bucket
}

resource "databricks_mws_workspaces" "this" {
  provider       = databricks.accounts
  account_id     = var.account_id
  workspace_name = var.workspace_name
  aws_region     = var.region

  credentials_id           = databricks_mws_credentials.this.credentials_id
  storage_configuration_id = databricks_mws_storage_configurations.this.storage_configuration_id

  # Wait for IAM + bucket-policy propagation even when mws_credentials is already in state
  # (a re-run): the storage validation runs here, so this resource must gate on the delay.
  depends_on = [time_sleep.wait_for_iam]

  token {}
}

# ---------------------------------------------------------------------------
# Jobs cluster — 1 worker (UC-capable), auto-terminates after 20 min of inactivity
# ---------------------------------------------------------------------------
resource "databricks_cluster" "jobs" {
  provider     = databricks.workspace
  cluster_name = "${var.workspace_name}-jobs-cluster"
  # Hardcoded (NOT data sources): the databricks_spark_version / databricks_node_type data
  # sources query the workspace API, but the workspace provider's host = workspace_url is only
  # known AFTER the workspace is created in this same apply → "unsupported protocol scheme".
  # These pinned values come from configs/infra/databricks.yaml (LTS runtime + AWS node).
  # m5d (not m5): the "d" variant has local NVMe storage, so no EBS volume is required —
  # an EBS-only type (m5.xlarge) fails "At least one EBS volume must be attached".
  # DBR 18.2 (Spark 4.1, Java 17): the older 14.3 LTS (Java 8) HANGS on the RDS Postgres SSL
  # handshake during the JDBC read (verified: the read succeeds on an 18.2 cluster but stalls on
  # 14.3, same driver + sslmode=require + network). 18.2's newer JVM negotiates SSL cleanly.
  spark_version           = "18.2.x-scala2.13"
  node_type_id            = "m5d.xlarge"
  autotermination_minutes = 20

  # Unity Catalog requires a UC-capable access mode. SINGLE_USER (assigned to the pipeline's
  # service principal) is the right fit for a single-node jobs cluster — full Spark + libraries
  # (the JDBC Maven driver works, unlike shared mode). Without this: "UC_NOT_ENABLED ... Unity
  # Catalog is not enabled on this cluster". The pipeline job runs as this same SP (run_as).
  data_security_mode = "SINGLE_USER"
  single_user_name   = var.databricks_client_id

  # One dedicated worker — NOT single-node. A single-node cluster (num_workers = 0) only runs
  # tasks if spark.master=local[*] makes the driver act as the executor; but with a UC access
  # mode (SINGLE_USER) the spark.databricks.cluster.profile=singleNode / spark.master conf is
  # rejected ("not allowed when choosing an access mode"), and num_workers=0 WITHOUT it yields a
  # cluster with ZERO executors — every Spark stage hangs forever at 0/1 tasks (the JDBC read
  # never gets a task slot; observed as Stage 0 0/1, duration "Unknown"). A single worker gives a
  # real executor (SINGLE_USER is fully UC-compatible WITH workers), so stages actually run. This
  # is the smallest reliable UC jobs cluster — no single-node spark_conf ambiguity at all.
  num_workers = 1

  custom_tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

# ---------------------------------------------------------------------------
# SQL Warehouse — SERVERLESS, 2X-Small, auto-stops after 10 minutes.
# Serverless = instant start (no ~7-min EC2 spin-up), zero idle cost (compute runs in the
# Databricks account, billed per-query), nothing to manage. Requires warehouse_type = PRO and
# serverless enabled at the account (Account console → Settings → Feature enablement).
# The jobs cluster stays CLASSIC — serverless jobs compute restricts the Maven JDBC driver the
# pipeline needs to read Postgres.
# ---------------------------------------------------------------------------
resource "databricks_sql_endpoint" "main" {
  provider                  = databricks.workspace
  name                      = "${var.workspace_name}-warehouse"
  cluster_size              = "2X-Small"
  auto_stop_mins            = 10
  max_num_clusters          = 1
  warehouse_type            = "PRO"
  enable_serverless_compute = true

  tags {
    custom_tags {
      key   = "Project"
      value = "multi-cloud-agent"
    }
    custom_tags {
      key   = "ManagedBy"
      value = "terraform-bootstrap"
    }
  }
}

# ---------------------------------------------------------------------------
# Unity Catalog — metastore, catalog, schema
#
# 🔴 ACCOUNT-LEVEL CONSTRAINT: a Databricks account may hold exactly ONE metastore per
# region. This bootstrap CREATES one (self-contained demo on a dedicated account). If the
# account already has a metastore in var.region, `terraform apply` will conflict — in that
# case reference the existing metastore via a `data "databricks_metastore"` and keep only the
# `databricks_metastore_assignment` (drop this resource). Teardown removes it entirely
# (force_destroy), so do NOT point a shared/production account at this bootstrap.
# ---------------------------------------------------------------------------
resource "databricks_metastore" "this" {
  provider      = databricks.accounts
  name          = var.metastore_name
  region        = var.region
  force_destroy = true # ephemeral demo — allow teardown even with catalogs attached
}

resource "databricks_metastore_assignment" "this" {
  provider     = databricks.accounts
  metastore_id = databricks_metastore.this.id
  workspace_id = databricks_mws_workspaces.this.workspace_id
}

resource "databricks_catalog" "main" {
  provider      = databricks.workspace
  metastore_id  = databricks_metastore.this.id
  name          = replace(var.workspace_name, "-", "_")
  comment       = "Primary catalog for ${var.workspace_name}"
  force_destroy = true # ephemeral demo — drop the catalog (and its schemas/tables) on teardown

  # Governed MANAGED storage: the Spark job uses saveAsTable (managed tables), so pin the
  # catalog's managed location to a STRICT SUBPATH of the external location (which is /managed).
  # A strict subpath is unambiguously "within" the external location; without this, managed
  # tables fall back to the metastore default (or fail when the metastore has none).
  storage_root = "s3://${var.bucket_name}/managed/catalog"

  properties = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }

  # The external location (and its credential) must register the bucket before the catalog
  # can claim a managed root inside it.
  depends_on = [databricks_metastore_assignment.this, databricks_external_location.s3]
}

resource "databricks_schema" "raw" {
  provider     = databricks.workspace
  catalog_name = databricks_catalog.main.name
  name         = "raw"
  comment      = "Raw ingestion layer"

  properties = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

# ---------------------------------------------------------------------------
# Catalog read grant — REQUIRED for humans to view the Lakeview observability dashboard.
# Unity Catalog access is EXPLICIT: even an account/workspace admin needs a grant (or to be a
# metastore admin) to USE a catalog. The catalog is created/owned by the service principal (this
# provider runs as the SP), so a human opening the dashboard runs its queries as themselves and
# hits "[INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG". Granting the built-in
# "account users" group USE_CATALOG + USE_SCHEMA + SELECT (cascades to every schema/table) lets
# any account user query the Delta tables, so the dashboard widgets render. Read-only — no MODIFY.
# ---------------------------------------------------------------------------
resource "databricks_grants" "catalog_read" {
  provider = databricks.workspace
  catalog  = databricks_catalog.main.name

  grant {
    principal  = "account users"
    privileges = ["USE_CATALOG", "USE_SCHEMA", "SELECT"]
  }

  depends_on = [databricks_schema.raw]
}

# ---------------------------------------------------------------------------
# IAM role for Unity Catalog storage credential (S3 access)
#
# Unity Catalog needs a SELF-ASSUMING role: the trust must list BOTH the Databricks UC master
# role AND this role's own ARN, with external_id = the Databricks ACCOUNT id (not the metastore
# id). A hand-written trust that omits the self-assume or uses the wrong external_id fails the
# external-location validation with "403 Forbidden ... does not have READ permissions". The
# official data sources generate the exact trust + S3/STS policy Databricks expects. The self-ARN
# is built from account id + role name (a literal), so there is no resource self-reference cycle.
# ---------------------------------------------------------------------------
data "aws_caller_identity" "current" {}

locals {
  uc_role_name = "${var.workspace_name}-unity-catalog-role"
}

data "databricks_aws_unity_catalog_assume_role_policy" "this" {
  provider       = databricks.accounts
  aws_account_id = data.aws_caller_identity.current.account_id
  role_name      = local.uc_role_name
  external_id    = var.account_id
}

data "databricks_aws_unity_catalog_policy" "this" {
  provider       = databricks.accounts
  aws_account_id = data.aws_caller_identity.current.account_id
  bucket_name    = var.bucket_name
  role_name      = local.uc_role_name
}

resource "aws_iam_role" "unity_catalog" {
  name               = local.uc_role_name
  assume_role_policy = data.databricks_aws_unity_catalog_assume_role_policy.this.json

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_iam_role_policy" "unity_catalog" {
  name   = "unity-catalog-s3-policy"
  role   = aws_iam_role.unity_catalog.id
  policy = data.databricks_aws_unity_catalog_policy.this.json
}

# IAM eventual consistency — the storage-credential / external-location validation assumes this
# role the instant it is created. Same 30s settle as the cross-account role.
resource "time_sleep" "wait_for_uc_iam" {
  depends_on      = [aws_iam_role.unity_catalog, aws_iam_role_policy.unity_catalog]
  create_duration = "30s"
  triggers = {
    role_arn = aws_iam_role.unity_catalog.arn
  }
}

# ---------------------------------------------------------------------------
# Unity Catalog storage credential + external location
# ---------------------------------------------------------------------------
resource "databricks_storage_credential" "s3" {
  provider = databricks.workspace
  name     = "${var.workspace_name}-s3-credential"

  aws_iam_role {
    role_arn = aws_iam_role.unity_catalog.arn
  }

  comment = "Storage credential for S3 bucket ${var.bucket_name}"

  depends_on = [databricks_metastore_assignment.this, time_sleep.wait_for_uc_iam]
}

resource "databricks_external_location" "s3" {
  provider = databricks.workspace
  name     = "${var.workspace_name}-s3-location"
  # Subpath, NOT the bucket root: the bucket root is the workspace DBFS storage configuration,
  # and an external location may not overlap workspace storage ("conflicts with a storage
  # configuration"). /managed is the catalog's managed storage_root, so this covers exactly it.
  url             = "s3://${var.bucket_name}/managed"
  credential_name = databricks_storage_credential.s3.name
  comment         = "External location for the Unity Catalog managed storage (s3://${var.bucket_name}/managed)"

  depends_on = [databricks_storage_credential.s3]
}
