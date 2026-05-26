# ── Cloud SQL: MySQL instance ─────────────────────────────────────────────────

resource "google_sql_database_instance" "main" {
  name             = var.db_instance_name
  database_version = "MYSQL_8_0"
  region           = var.region
  project          = var.project_id

  deletion_protection = false

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_type         = "PD_SSD"

    backup_configuration {
      enabled = true
    }

    # Publicly accessible so seed_chaos.py can connect from local machine.
    # In production, use Cloud SQL Auth Proxy or Private IP.
    ip_configuration {
      ipv4_enabled = true

      dynamic "authorized_networks" {
        for_each = length(var.db_password) > 0 ? [1] : []
        content {
          name  = "allow-all-dev"
          value = "0.0.0.0/0"
        }
      }
    }

    database_flags {
      name  = "max_connections"
      value = "100"
    }
  }

  depends_on = [google_project_service.sqladmin]
}

resource "google_sql_database" "main" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
  project  = var.project_id
}

resource "google_sql_user" "pipeline" {
  name     = var.db_user
  instance = google_sql_database_instance.main.name
  password = var.db_password
  project  = var.project_id
}
