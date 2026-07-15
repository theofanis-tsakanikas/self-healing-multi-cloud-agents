data "aws_caller_identity" "current" {}

locals {
  oidc_issuer_host = replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")
}

# ── Runtime Service Account: SSM read permissions ────────────────────────────
# self-healing-agent-svc reads DB credentials from SSM at runtime.
# This is the only permission the runtime user needs — no DB passwords in env vars.

resource "aws_iam_user_policy" "svc_ssm_read" {
  name = "multi-cloud-self-healing-agent-ssm-read"
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
        # Required to decrypt SecureString parameters (rds_password)
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

# ── EKS OIDC Provider (required for IRSA) ────────────────────────────────────

data "tls_certificate" "eks" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

# ── EKS Cluster Role ─────────────────────────────────────────────────────────

resource "aws_iam_role" "eks_cluster" {
  name = var.cluster_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSComputePolicy",
    "arn:aws:iam::aws:policy/AmazonEKSBlockStoragePolicy",
    "arn:aws:iam::aws:policy/AmazonEKSLoadBalancingPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSNetworkingPolicy",
  ])

  role       = aws_iam_role.eks_cluster.name
  policy_arn = each.value
}

resource "aws_iam_role_policy" "eks_cluster_instance_profile" {
  name = "eks-auto-mode-instance-profile"
  role = aws_iam_role.eks_cluster.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "iam:CreateInstanceProfile",
          "iam:DeleteInstanceProfile",
          "iam:GetInstanceProfile",
          "iam:TagInstanceProfile",
          "iam:AddRoleToInstanceProfile",
          "iam:RemoveRoleFromInstanceProfile",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.eks_node.arn
      }
    ]
  })
}

# ── EKS Node Role ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "eks_node" {
  name = var.node_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "eks_node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodeMinimalPolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly",
  ])

  role       = aws_iam_role.eks_node.name
  policy_arn = each.value
}

# ── EKS Access: Bootstrap Admin ───────────────────────────────────────────────

resource "aws_eks_access_entry" "bootstrap_admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/bootstrap-admin"
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "bootstrap_admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/bootstrap-admin"
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.bootstrap_admin]
}

# ── EKS Access: CI Pipeline User ─────────────────────────────────────────────

resource "aws_eks_access_entry" "pipeline_user" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.pipeline_iam_arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "pipeline_user_admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.pipeline_iam_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.pipeline_user]
}

# ── IRSA: eu-sales-insights service account ───────────────────────────────────

resource "aws_iam_role" "irsa_eu_sales" {
  name = "eu-sales-insights-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/${local.oidc_issuer_host}"
      }
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_host}:sub" = "system:serviceaccount:analytics:eu-sales-insights-sa"
          "${local.oidc_issuer_host}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_iam_role_policy" "irsa_eu_sales" {
  name = "eu-sales-s3-access"
  role = aws_iam_role.irsa_eu_sales.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::eu-sales-insights-data",
          "arn:aws:s3:::eu-sales-insights-data/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabases", "glue:GetDatabase", "glue:CreateDatabase",
          "glue:GetTables", "glue:GetTable", "glue:CreateTable",
          "glue:UpdateTable", "glue:DeleteTable",
          "glue:GetPartitions", "glue:GetPartition", "glue:BatchGetPartition",
          "glue:BatchCreatePartition", "glue:BatchDeletePartition", "glue:UpdatePartition",
        ]
        # Scoped to THIS account+region's Glue catalog/databases/tables (was Resource="*", which also
        # granted every OTHER account/region if the policy were ever reused).
        Resource = [
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:database/*",
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/*/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath",
        ]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/multi-cloud-self-healing-agent/*"
      }
    ]
  })
}

# ── IRSA: shared role for NL/Streamlit-authored pipelines ─────────────────────
# The bootstrap pre-creates a dedicated IRSA role per VALIDATED pipeline (eu_sales
# above). A pipeline authored at runtime through the NL/Streamlit surface has a
# brand-new ServiceAccount + bucket that no pre-created role trusts, so it could
# never read SSM creds (cloud_get -> None) or write S3. This SHARED role closes that
# gap: its trust is a StringLike wildcard over the analytics SA naming convention
# (`<slug>-insights-sa`) and its policy is scoped to the `*-insights-data` bucket
# convention + the project SSM namespace + Glue. The NL build points each authored
# pipeline's SA annotation at this role (see _build_from_answers -> pipeline_irsa_role_name).
# Demo trade-off (documented in SECURITY.md): broader than the per-pipeline roles —
# any analytics `*-insights-sa` may assume it, scoped to the bucket naming convention.
resource "aws_iam_role" "irsa_pipelines" {
  name = "multi-cloud-pipelines-irsa"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/${local.oidc_issuer_host}"
      }
      Condition = {
        StringLike = {
          "${local.oidc_issuer_host}:sub" = "system:serviceaccount:analytics:*-insights-sa"
        }
        StringEquals = {
          "${local.oidc_issuer_host}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_iam_role_policy" "irsa_pipelines" {
  name = "pipelines-s3-glue-ssm"
  role = aws_iam_role.irsa_pipelines.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::*-insights-data",
          "arn:aws:s3:::*-insights-data/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabases", "glue:GetDatabase", "glue:CreateDatabase",
          "glue:GetTables", "glue:GetTable", "glue:CreateTable",
          "glue:UpdateTable", "glue:DeleteTable",
          "glue:GetPartitions", "glue:GetPartition", "glue:BatchGetPartition",
          "glue:BatchCreatePartition", "glue:BatchDeletePartition", "glue:UpdatePartition",
        ]
        # Scoped to THIS account+region's Glue catalog/databases/tables (was Resource="*", which also
        # granted every OTHER account/region if the policy were ever reused).
        Resource = [
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:database/*",
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/*/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath",
        ]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/multi-cloud-self-healing-agent/*"
      }
    ]
  })
}
