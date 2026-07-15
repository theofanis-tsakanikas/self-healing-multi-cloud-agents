# UNSAFE FIXTURE — trips the generated-Terraform HIGH rules (TF_PUBLIC_DB, TF_IAM_WILDCARD_RESOURCE,
# TF_OPEN_INGRESS, TF_PUBLIC_BUCKET_ACL). Not real infra; only fed to the gate in tests.
resource "aws_db_instance" "bad" {
  publicly_accessible = true
}

resource "aws_iam_policy" "bad" {
  # wildcard as a NON-first list element (the naive `= "*"` regex would miss this)
  policy = jsonencode({ Statement = [{ Effect = "Allow", Action = "s3:GetObject", Resource = ["arn:aws:s3:::b/*", "*"] }] })
}

resource "aws_security_group" "bad" {
  ingress {
    cidr_blocks      = ["10.0.0.0/8"]
    ipv6_cidr_blocks = ["::/0"] # IPv6 open ingress
  }
}

resource "aws_s3_bucket_public_access_block" "bad" {
  block_public_acls = false # the modern way a bucket goes public
}
