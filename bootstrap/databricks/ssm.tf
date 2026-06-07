# ---------------------------------------------------------------------------
# SSM Parameter Store — the Lakehouse source DB connection (host_cloud = aws).
#
# Single source of truth, exactly like bootstrap/aws/ssm.tf: the bootstrap that CREATES the
# RDS publishes its connection here. The pipeline terraform reads these (data "aws_ssm_parameter")
# into the Databricks secret scope + job params; scripts/seed_chaos.py reads them via
# cloud_get("aws", "lakehouse_db_*"). No GitHub secret, no .env, no human-set password.
#
# Distinct `lakehouse_db_*` keys (not `rds_*`) so they never collide with the AWS eu_sales
# params in the same /aws/ namespace.
# ---------------------------------------------------------------------------
locals {
  lakehouse_ssm_prefix = "/multi-cloud-self-healing-agent/aws"
}

resource "aws_ssm_parameter" "lakehouse_db_host" {
  name  = "${local.lakehouse_ssm_prefix}/lakehouse_db_host"
  type  = "String"
  value = aws_db_instance.source_db.address
}

resource "aws_ssm_parameter" "lakehouse_db_port" {
  name  = "${local.lakehouse_ssm_prefix}/lakehouse_db_port"
  type  = "String"
  value = "5432"
}

resource "aws_ssm_parameter" "lakehouse_db_user" {
  name  = "${local.lakehouse_ssm_prefix}/lakehouse_db_user"
  type  = "String"
  value = var.db_username
}

resource "aws_ssm_parameter" "lakehouse_db_name" {
  name  = "${local.lakehouse_ssm_prefix}/lakehouse_db_name"
  type  = "String"
  value = var.db_name
}

resource "aws_ssm_parameter" "lakehouse_db_password" {
  name  = "${local.lakehouse_ssm_prefix}/lakehouse_db_password"
  type  = "SecureString"
  value = random_password.source_db.result
}

# ---------------------------------------------------------------------------
# The CI agent user (self-healing-agent-svc) reads this connection from SSM — both
# read_data_schema (architect schema phase) and the pipeline terraform's
# `data "aws_ssm_parameter"`. The AWS bootstrap grants the same (bootstrap/aws/iam.tf
# svc_ssm_read), but it may be torn down — so the Databricks bootstrap grants it too, keeping
# the demo self-contained. Distinct policy name so it never collides with the AWS one.
# kms:Decrypt is required for the SecureString password (GetParameter WithDecryption=True).
resource "aws_iam_user_policy" "svc_ssm_read_lakehouse" {
  name = "multi-cloud-lakehouse-ssm-read"
  user = "self-healing-agent-svc"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath",
        ]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/multi-cloud-self-healing-agent/*"
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.region}.amazonaws.com"
          }
        }
      },
    ]
  })
}
