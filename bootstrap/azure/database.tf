# ── PostgreSQL Flexible Server ────────────────────────────────────────────────

resource "azurerm_postgresql_flexible_server" "main" {
  name                   = var.db_server_name
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  version                = "16"
  administrator_login    = var.db_username
  administrator_password = var.db_password

  # Burstable B1ms is sufficient for dev/test
  sku_name   = "B_Standard_B1ms"
  storage_mb = 32768

  # Publicly accessible for seed_chaos.py from local machine
  # Set to false and use Private Link in production
  public_network_access_enabled = true

  backup_retention_days        = 7
  geo_redundant_backup_enabled = false

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }

  # Azure auto-assigns an availability zone when none is specified. Without this,
  # every subsequent apply tries to "reset" the zone and fails with
  # "zone can only be changed when exchanged with standby_availability_zone".
  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = var.db_name
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "utf8"
}

# Allow access from all IPs if db_allowed_cidrs is empty (dev convenience)
# Replace with specific CIDR ranges in production
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_access" {
  count            = length(var.db_allowed_cidrs) > 0 ? length(var.db_allowed_cidrs) : 1
  name             = length(var.db_allowed_cidrs) > 0 ? "allow-cidr-${count.index}" : "allow-all-dev"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = length(var.db_allowed_cidrs) > 0 ? cidrhost(var.db_allowed_cidrs[count.index], 0) : "0.0.0.0"
  end_ip_address   = length(var.db_allowed_cidrs) > 0 ? cidrhost(var.db_allowed_cidrs[count.index], -1) : "255.255.255.255"
}
