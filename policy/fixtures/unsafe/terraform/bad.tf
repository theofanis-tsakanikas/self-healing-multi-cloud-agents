# UNSAFE FIXTURE — trips the generated-Terraform HIGH rules (TF_PUBLIC_DB, TF_IAM_WILDCARD_RESOURCE,
# TF_OPEN_INGRESS, TF_PUBLIC_BUCKET_ACL). Not real infra; only fed to the gate in tests.
resource "aws_db_instance" "bad" {
  publicly_accessible = true
}

resource "aws_iam_policy" "bad" {
  policy = jsonencode({ Statement = [{ Effect = "Allow", Action = "s3:*", Resource = "*" }] })
}

resource "aws_security_group" "bad" {
  ingress {
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_s3_bucket_acl" "bad" {
  acl = "public-read-write"
}
