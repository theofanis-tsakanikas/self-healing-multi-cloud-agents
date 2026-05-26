# ─────────────────────────────────────────────────────────────────────────────
# SSM Parameter Store — infra outputs written automatically on terraform apply.
#
# Convention: /multi-cloud-self-healing-agent/<cloud>/<key>
#
# Passwords → SecureString (encrypted at rest with AWS-managed KMS key).
# Everything else → String.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  ssm_prefix = "/multi-cloud-self-healing-agent/aws"
  ssm_tags = {
    Project   = "multi-cloud-self-healing-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

# ── RDS ──────────────────────────────────────────────────────────────────────

resource "aws_ssm_parameter" "rds_host" {
  name  = "${local.ssm_prefix}/rds_host"
  type  = "String"
  value = aws_db_instance.eu_sales_raw.address
  tags  = local.ssm_tags
}

resource "aws_ssm_parameter" "rds_port" {
  name  = "${local.ssm_prefix}/rds_port"
  type  = "String"
  value = tostring(aws_db_instance.eu_sales_raw.port)
  tags  = local.ssm_tags
}

resource "aws_ssm_parameter" "rds_db_name" {
  name  = "${local.ssm_prefix}/rds_db_name"
  type  = "String"
  value = aws_db_instance.eu_sales_raw.db_name
  tags  = local.ssm_tags
}

resource "aws_ssm_parameter" "rds_username" {
  name  = "${local.ssm_prefix}/rds_username"
  type  = "String"
  value = aws_db_instance.eu_sales_raw.username
  tags  = local.ssm_tags
}

resource "aws_ssm_parameter" "rds_password" {
  name  = "${local.ssm_prefix}/rds_password"
  type  = "SecureString"
  value = random_password.rds.result
  tags  = local.ssm_tags
}

# ── EKS ──────────────────────────────────────────────────────────────────────

resource "aws_ssm_parameter" "eks_cluster_name" {
  name  = "${local.ssm_prefix}/eks_cluster_name"
  type  = "String"
  value = aws_eks_cluster.main.name
  tags  = local.ssm_tags
}

resource "aws_ssm_parameter" "eks_cluster_endpoint" {
  name  = "${local.ssm_prefix}/eks_cluster_endpoint"
  type  = "String"
  value = aws_eks_cluster.main.endpoint
  tags  = local.ssm_tags
}

resource "aws_ssm_parameter" "irsa_role_arn" {
  name  = "${local.ssm_prefix}/irsa_role_arn"
  type  = "String"
  value = aws_iam_role.irsa_eu_sales.arn
  tags  = local.ssm_tags
}

# ── ECR / S3 / DynamoDB ───────────────────────────────────────────────────────

resource "aws_ssm_parameter" "ecr_repository_url" {
  name  = "${local.ssm_prefix}/ecr_repository_url"
  type  = "String"
  value = aws_ecr_repository.pipeline.repository_url
  tags  = local.ssm_tags
}

resource "aws_ssm_parameter" "state_bucket_name" {
  name  = "${local.ssm_prefix}/state_bucket_name"
  type  = "String"
  value = aws_s3_bucket.tf_state.bucket
  tags  = local.ssm_tags
}
