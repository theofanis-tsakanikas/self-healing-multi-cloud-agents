output "job_id"       { value = databricks_job.pipeline.id }
output "job_url"      { value = databricks_job.pipeline.url }
output "dashboard_id" { value = databricks_dashboard.observability.id }