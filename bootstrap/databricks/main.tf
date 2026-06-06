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

# ---------------------------------------------------------------------------
# Databricks workspace (MWS — multi-workspace / E2 deployment model)
# ---------------------------------------------------------------------------
resource "databricks_mws_credentials" "this" {
  provider         = databricks.accounts
  account_id       = var.account_id
  credentials_name = "${var.workspace_name}-credentials"
  role_arn         = aws_iam_role.cross_account.arn
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

  token {}
}

# ---------------------------------------------------------------------------
# Jobs cluster — single-node, auto-terminates after 20 minutes of inactivity
# ---------------------------------------------------------------------------
resource "databricks_cluster" "jobs" {
  provider                = databricks.workspace
  cluster_name            = "${var.workspace_name}-jobs-cluster"
  spark_version           = data.databricks_spark_version.latest_lts.id
  node_type_id            = data.databricks_node_type.smallest.id
  autotermination_minutes = 20

  # Single-node cluster — no workers needed for pipeline jobs
  num_workers = 0

  spark_conf = {
    "spark.databricks.cluster.profile" = "singleNode"
    "spark.master"                     = "local[*]"
  }

  custom_tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

data "databricks_spark_version" "latest_lts" {
  provider          = databricks.workspace
  long_term_support = true
}

data "databricks_node_type" "smallest" {
  provider   = databricks.workspace
  local_disk = false
}

# ---------------------------------------------------------------------------
# SQL Warehouse — 2X-Small, auto-stops after 10 minutes
# ---------------------------------------------------------------------------
resource "databricks_sql_endpoint" "main" {
  provider                  = databricks.workspace
  name                      = "${var.workspace_name}-warehouse"
  cluster_size              = "2X-Small"
  auto_stop_mins            = 10
  max_num_clusters          = 1
  enable_serverless_compute = false

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
  # catalog's managed location to a subprefix of our own external location. Without this,
  # managed tables fall back to the metastore default (or fail when the metastore has none).
  storage_root = "s3://${var.bucket_name}/managed"

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
# IAM role for Unity Catalog storage credential (S3 access)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "unity_catalog_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::414351767826:root"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [databricks_metastore.this.id]
    }
  }
}

resource "aws_iam_role" "unity_catalog" {
  name               = "${var.workspace_name}-unity-catalog-role"
  assume_role_policy = data.aws_iam_policy_document.unity_catalog_assume.json

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

data "aws_iam_policy_document" "unity_catalog_s3" {
  statement {
    sid    = "UnityCatalogS3"
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

resource "aws_iam_role_policy" "unity_catalog" {
  name   = "unity-catalog-s3-policy"
  role   = aws_iam_role.unity_catalog.id
  policy = data.aws_iam_policy_document.unity_catalog_s3.json
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

  depends_on = [databricks_metastore_assignment.this]
}

resource "databricks_external_location" "s3" {
  provider        = databricks.workspace
  name            = "${var.workspace_name}-s3-location"
  url             = "s3://${var.bucket_name}"
  credential_name = databricks_storage_credential.s3.name
  comment         = "External location pointing to s3://${var.bucket_name}"

  depends_on = [databricks_storage_credential.s3]
}
