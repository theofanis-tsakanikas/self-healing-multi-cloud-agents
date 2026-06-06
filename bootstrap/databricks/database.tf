# ---------------------------------------------------------------------------
# Source RDS Postgres — the Lakehouse demo's OLTP source.
#
# Self-contained, exactly like every other cloud's bootstrap provisions its own source DB
# (bootstrap/aws/rds.tf, bootstrap/azure/database.tf, bootstrap/gcp/database.tf). The Lakehouse
# therefore does NOT depend on the AWS bootstrap. Seeded by scripts/seed_chaos.py
# (--target sales_lakehouse) and read by the Spark job via JDBC. Distinct names from the AWS
# eu_sales source: db `lakehouse_raw`, table `raw_sales_lakehouse`.
# ---------------------------------------------------------------------------
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "source_db" {
  name        = "sales-lakehouse-rds-sg"
  description = "PostgreSQL access to the Lakehouse source DB (Databricks cluster + seeding)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "PostgreSQL"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.rds_allowed_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_db_subnet_group" "source_db" {
  name       = "sales-lakehouse-rds-subnet-group"
  subnet_ids = data.aws_subnets.default.ids

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

# Auto-generate the password (like bootstrap/aws/rds.tf) — never human-set, never a GitHub
# secret. Stored only in SSM (ssm.tf), read from there by the pipeline terraform + seeding.
resource "random_password" "source_db" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}|;"
}

resource "aws_db_instance" "source_db" {
  identifier        = "sales-lakehouse-raw-data"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = "db.t4g.micro"
  allocated_storage = 20

  db_name  = var.db_name     # lakehouse_raw — distinct from the AWS eu_sales "sales_raw"
  username = var.db_username # postgres
  password = random_password.source_db.result

  db_subnet_group_name   = aws_db_subnet_group.source_db.name
  vpc_security_group_ids = [aws_security_group.source_db.id]

  # The Databricks-managed cluster and seed_chaos.py reach it over the internet.
  publicly_accessible = true
  skip_final_snapshot = true
  deletion_protection = false

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}
