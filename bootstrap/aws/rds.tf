data "aws_subnet" "first" {
  id = tolist(data.aws_subnets.default.ids)[0]
}

resource "aws_security_group" "rds" {
  name        = "eu-sales-rds-sg"
  description = "Allow PostgreSQL access from EKS and developer machines"
  vpc_id      = data.aws_subnet.first.vpc_id

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

resource "aws_db_subnet_group" "rds" {
  name       = "eu-sales-rds-subnet-group"
  subnet_ids = data.aws_subnets.default.ids

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_db_instance" "eu_sales_raw" {
  identifier        = "eu-sales-raw-data"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = "db.t4g.micro"
  allocated_storage = 20

  db_name  = var.rds_db_name
  username = var.rds_username
  password = var.rds_password

  db_subnet_group_name   = aws_db_subnet_group.rds.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # Publicly accessible so seed_chaos.py can connect from a local machine
  publicly_accessible = true

  skip_final_snapshot = true
  deletion_protection = false

  tags = {
    Project   = "multi-cloud-agent"
    ManagedBy = "terraform-bootstrap"
  }
}
