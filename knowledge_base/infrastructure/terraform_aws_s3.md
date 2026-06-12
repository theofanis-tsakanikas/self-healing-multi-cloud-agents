---
id: terraform-aws-s3
applies_to: aws
primary_consumer: infra-agent   # retrieved via Pinecone (query_vector_store); medic may also retrieve it
enforced_by: validate_generated_code (safety net) + agent prompts
last_reviewed: 2026-06-11
---

# STANDARD: TERRAFORM AWS S3 & BACKEND
This standard defines the mandatory configuration for AWS S3 resources and Terraform state management to ensure compliance and avoid provider deprecation warnings.

## FILE RESPONSIBILITIES (strictly enforced)
Each file has an exclusive responsibility — never duplicate blocks across files:

| File | Contains | Must NOT contain |
|---|---|---|
| `providers.tf` | `terraform { backend "s3" {} }` + `provider "aws" {}` | resource blocks |
| `main.tf` | resource blocks ONLY | `terraform {}`, `provider {}` |
| `variables.tf` | `variable` declarations | anything else |
| `outputs.tf` | `output` declarations | anything else |
| `terraform.tfvars` | variable values | HCL blocks |

**CRITICAL: If `providers.tf` already exists, `main.tf` must NEVER include a `terraform {}` or `provider {}` block. Doing so causes "Duplicate backend/provider" errors.**

---

## MANDATORY RESOURCE CHECKLIST
Every `main.tf` generated for an AWS S3 pipeline MUST contain ALL of the following resources — no exceptions:

| Resource | Purpose |
|---|---|
| `aws_s3_bucket` | Core bucket |
| `aws_s3_bucket_ownership_controls` | Disable ACLs |
| `aws_s3_bucket_public_access_block` | Block all public access |
| `aws_s3_bucket_versioning` | Enable versioning |
| `aws_s3_bucket_server_side_encryption_configuration` | KMS encryption |
| `aws_s3_bucket_lifecycle_configuration` | Storage tiering |
| `aws_iam_policy` | Scoped S3 access policy |

`outputs.tf` MUST export exactly: `bucket_name`, `bucket_arn`, `iam_policy_arn`, `region`.

`terraform.tfvars` MUST be generated with concrete values for all declared variables — Terraform loads it automatically so no `-var` flags are needed at apply time.

If any of these is missing, the configuration is incomplete.

---

## 1. TERRAFORM BACKEND CONFIGURATION
- **Mandatory S3 Backend:** Use the S3 backend for state storage.
- **Literal Value Constraint:** In the `terraform { backend "s3" { ... } }` block, you MUST use concrete string literals for `bucket`, `key`, `region`, and `dynamodb_table`. Variables (`var.*`) are NOT permitted in the backend block.
- **Locking:** DynamoDB locking is mandatory. The DynamoDB table must have a primary key named `LockID` (string).
- **Version Pinning:** `required_version` and `required_providers` are MANDATORY — without them `terraform init` downloads the latest provider version which may introduce breaking changes silently.
- **Provider Block:** `providers.tf` MUST also include a `provider "aws"` block using `var.region` — this is separate from the backend region and controls where AWS resources are created.

The complete `providers.tf` MUST look exactly like this:

```hcl
terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "<state_bucket>"        # = CLOUD_SETUP.state_bucket — the bootstrap STATE bucket; NEVER the data bucket / var.bucket_name (the data bucket is CREATED by this apply and does not exist at init time)
    key            = "<state_key>"           # = CLOUD_SETUP.state_key, copied verbatim — do NOT derive from pipeline-name
    region         = "<concrete-region>"
    dynamodb_table = "<lock_table>"          # = CLOUD_SETUP.lock_table — the lock table bootstrap creates
  }
}

provider "aws" {
  region = var.region
}
```

---

## 2. S3 BUCKET PROVISIONING (AWS PROVIDER 5+)
To avoid `AccessControlListNotSupported` and deprecation warnings, follow the "Resource Splitting" pattern:

### 2.1 Core Bucket Resource
- Use ONLY `bucket`, `tags` inside the `aws_s3_bucket` resource. `force_destroy` is FORBIDDEN.
- **NO NESTED BLOCKS:** Do not use legacy nested blocks like `versioning { }`, `acl = "private"`, or `server_side_encryption_configuration { }`.
- **Bucket name MUST use `var.bucket_name`** — never hardcode a string literal.
- **Resource name is FIXED: always `data_bucket`** — never rename it. Renaming causes Terraform to plan a destroy+create cycle on a bucket that already exists, resulting in a 409 error.
- **`prevent_destroy = true` is MANDATORY** to guard against accidental destruction:
```hcl
resource "aws_s3_bucket" "data_bucket" {
  bucket = var.bucket_name
  tags   = { Project = var.project_id }
  lifecycle {
    prevent_destroy = true
  }
}
```

### 2.2 Ownership & Access Control
- **Ownership:** Use `aws_s3_bucket_ownership_controls`. The `object_ownership` attribute is **nested** inside a `rule` block — placing it at the top level causes "Unsupported argument" and "Insufficient rule blocks" errors:
```hcl
resource "aws_s3_bucket_ownership_controls" "ownership_controls" {
  bucket = aws_s3_bucket.<name>.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
```
- **Public Access Block:** Always implement `aws_s3_bucket_public_access_block` setting all four block flags to `true`. **Resource name is FIXED: always `public_access_block`** — renaming it causes Terraform to plan a destroy+create cycle on the existing resource, which triggers unnecessary AWS API calls:
```hcl
resource "aws_s3_bucket_public_access_block" "public_access_block" {
  bucket                  = aws_s3_bucket.data_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
```

### 2.3 Separate Service Resources
Link these resources to the main bucket using `bucket = aws_s3_bucket.<name>.id`:

- **Versioning:** Use `aws_s3_bucket_versioning`. The `status` field is **nested** inside a `versioning_configuration` block — `enabled = true` is NOT a valid attribute and will fail:
```hcl
resource "aws_s3_bucket_versioning" "versioning" {
  bucket = aws_s3_bucket.<name>.id
  versioning_configuration {
    status = "Enabled"
  }
}
```

- **Encryption:** Use `aws_s3_bucket_server_side_encryption_configuration` with `sse_algorithm = "aws:kms"` nested inside `rule > apply_server_side_encryption_by_default`.

- **Lifecycle:** Use `aws_s3_bucket_lifecycle_configuration`. Every rule MUST include a `filter {}` block (even if empty) — omitting it causes a provider error in AWS Provider 4+. Use a two-tier transition: 90 days → `STANDARD_IA` (still queryable, lower cost), 365 days → `GLACIER` (archive). Do NOT transition to GLACIER before 90 days — daily pipeline data queried for trend analysis within the first 3 months would require expensive Glacier restores:
```hcl
resource "aws_s3_bucket_lifecycle_configuration" "lifecycle" {
  bucket = aws_s3_bucket.<name>.id
  rule {
    id     = "lifecycle_rule"
    status = "Enabled"
    filter {}
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
  }
}
```

---

## 3. IAM & ACCESS POLICY
- **Policy Syntax:** Every statement in `jsonencode` must include `Effect = "Allow"` (or "Deny"). Omitting this causes `MalformedPolicyDocument`.
- **Three mandatory statements — all required:**
    - Statement 1: `s3:ListBucket` on the **Bucket ARN**.
    - Statement 2: `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on the **Bucket ARN + `/processed/*`**.
    - Statement 3: Glue permissions on `"*"` — Trino uses AWS Glue Data Catalog as metastore. Without these, `CREATE TABLE`, `DROP TABLE`, and `CALL sync_partition_metadata` all fail with access denied. Glue ARNs are complex and region/account-scoped internally — always use `Resource = ["*"]` for Glue statements.
- **Least Privilege:** For dedicated buckets (one pipeline per bucket), restrict Statement 2 to `/processed/*` — the pipeline writes exclusively to `{bucket}/processed/run_date=.../`. Granting `/*` is over-permissive:
```hcl
# ✅ restricted to actual write path:
Resource = ["${aws_s3_bucket.data_bucket.arn}/processed/*"]
# ❌ over-permissive — full bucket access:
# Resource = ["${aws_s3_bucket.data_bucket.arn}/*"]
```
- **CRITICAL — `description` is mandatory and forces replacement if removed:** AWS treats `aws_iam_policy.description` as an immutable attribute — removing it from the config after initial creation causes Terraform to destroy and recreate the policy with a new ARN, breaking any IAM role attachments. Always include `description` AND add `lifecycle { ignore_changes = [description, tags] }`:
```hcl
resource "aws_iam_policy" "s3_access_policy" {
  name        = "${var.project_id}-s3-access-policy"
  description = "Scoped S3 and Glue access policy for ${var.project_id}"
  policy      = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.data_bucket.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.data_bucket.arn}/processed/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabase", "glue:GetDatabases", "glue:CreateDatabase",
          "glue:GetTable",    "glue:GetTables",    "glue:CreateTable",
          "glue:DeleteTable", "glue:UpdateTable",
          "glue:GetPartition",     "glue:GetPartitions",
          "glue:CreatePartition",  "glue:DeletePartition",  "glue:UpdatePartition",
          "glue:BatchCreatePartition", "glue:BatchDeletePartition"
        ]
        Resource = ["*"]
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        # The /multi-cloud-self-healing-agent/ prefix is the project's SSM namespace
        # (the same one cloud_get() reads) — change it only together with the bootstrap.
        Resource = "arn:aws:ssm:*:*:parameter/multi-cloud-self-healing-agent/*"
      }
    ]
  })
  lifecycle {
    ignore_changes = [description, tags]
  }
}
```

---

## 4. NAMING & TAGGING
- **Prefixing:** All resources must be prefixed with the `project_id`.
- **Variables:** `variables.tf` MUST declare the following three variables — no hardcoding anywhere in `main.tf`:

```hcl
variable "region" {
  description = "The AWS region to deploy resources in"
  type        = string
}

variable "bucket_name" {
  description = "Name of the S3 data bucket"
  type        = string
}

variable "project_id" {
  description = "Unique project identifier used for resource naming and tagging"
  type        = string
}
```

These variables are consumed in `main.tf` as `var.region`, `var.bucket_name`, and `var.project_id`. Do not use `default` values — values are injected at runtime via `-var` flags or `terraform.tfvars`.

---

## 5. TERRAFORM VARIABLE VALUES (terraform.tfvars)
Generate `terraform.tfvars` alongside the other four files. Populate it with the concrete values from the provided context — never use placeholders:

```hcl
region      = "<PROJECT_METADATA.region>"
bucket_name = "<CLOUD_SETUP.bucket_name>"
project_id  = "<PROJECT_METADATA.project_id>"
```

Terraform auto-loads `terraform.tfvars` at `plan`/`apply` time. Do **not** pass `-var` flags in the `execute_terraform` call.

---

## 6. MANDATORY OUTPUTS
`outputs.tf` MUST export exactly these four values — no more, no less:

```hcl
output "bucket_name" {
  value = aws_s3_bucket.data_bucket.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.data_bucket.arn
}

output "iam_policy_arn" {
  value = aws_iam_policy.s3_access_policy.arn
}

output "region" {
  value = var.region
}
```
