resource "aws_s3_bucket" "data_bucket" {
  bucket = var.bucket_name
  tags   = { Project = var.project_id }
}

resource "aws_s3_bucket_ownership_controls" "ownership_controls" {
  bucket = aws_s3_bucket.data_bucket.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "public_access_block" {
  bucket                  = aws_s3_bucket.data_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "versioning" {
  bucket = aws_s3_bucket.data_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "encryption" {
  bucket = aws_s3_bucket.data_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lifecycle" {
  bucket = aws_s3_bucket.data_bucket.id
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
        Resource = "arn:aws:ssm:*:*:parameter/multi-cloud-self-healing-agent/*"
      }
    ]
  })
  lifecycle {
    ignore_changes = [description, tags]
  }
}