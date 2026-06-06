output "bucket_name" {
  value = google_storage_bucket.data.name
}

output "bucket_url" {
  value = google_storage_bucket.data.url
}

output "service_account_email" {
  value = data.google_service_account.pipeline.email
}

output "service_account_id" {
  value = data.google_service_account.pipeline.id
}

output "project_id" {
  value = var.project_id
}