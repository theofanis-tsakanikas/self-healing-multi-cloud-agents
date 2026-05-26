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

output "rds_host" {
  description = "Set as POSTGRES_DB_HOST in .env"
  value       = aws_db_instance.eu_sales_raw.address
}

output "rds_port" {
  description = "Set as POSTGRES_DB_PORT in .env"
  value       = aws_db_instance.eu_sales_raw.port
}

output "rds_db_name" {
  description = "Set as POSTGRES_DB_NAME in .env"
  value       = aws_db_instance.eu_sales_raw.db_name
}

output "rds_username" {
  description = "Set as POSTGRES_DB_USER in .env"
  value       = aws_db_instance.eu_sales_raw.username
}
