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
