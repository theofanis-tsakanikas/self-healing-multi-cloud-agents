output "state_bucket_name" {
  value = aws_s3_bucket.tf_state.bucket
}

output "lock_table_name" {
  value = aws_dynamodb_table.tf_lock.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.pipeline.repository_url
}

output "eks_cluster_name" {
  value = aws_eks_cluster.main.name
}

output "eks_cluster_endpoint" {
  value = aws_eks_cluster.main.endpoint
}

output "eks_oidc_issuer" {
  value = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

output "irsa_role_arn" {
  description = "Annotate the eu-sales-insights-sa K8s service account with this ARN"
  value       = aws_iam_role.irsa_eu_sales.arn
}

output "aws_account_id" {
  description = "Account ID — used for the IRSA role ARN in NL-authored pipelines (00_namespaces.yaml)"
  value       = data.aws_caller_identity.current.account_id
}

output "oidc_provider_arn" {
  description = "EKS OIDC provider ARN — IRSA trust for NL-authored pipelines"
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "pipeline_irsa_role_name" {
  description = "Shared IRSA role for NL/Streamlit-authored pipelines — annotate <slug>-insights-sa with it"
  value       = aws_iam_role.irsa_pipelines.name
}

output "pipeline_irsa_role_arn" {
  description = "Shared IRSA role ARN for NL/Streamlit-authored pipelines"
  value       = aws_iam_role.irsa_pipelines.arn
}

output "rds_host" {
  description = "Written to SSM: /multi-cloud-self-healing-agent/aws/rds_host"
  value       = aws_db_instance.eu_sales_raw.address
}

output "rds_port" {
  description = "Written to SSM: /multi-cloud-self-healing-agent/aws/rds_port"
  value       = aws_db_instance.eu_sales_raw.port
}

output "rds_db_name" {
  description = "Written to SSM: /multi-cloud-self-healing-agent/aws/rds_db_name"
  value       = aws_db_instance.eu_sales_raw.db_name
}

output "rds_username" {
  description = "Written to SSM: /multi-cloud-self-healing-agent/aws/rds_username"
  value       = aws_db_instance.eu_sales_raw.username
}
