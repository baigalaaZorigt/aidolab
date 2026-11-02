# Shared Postgres (one database + role per app) and the file bucket (one prefix per app).

resource "aws_db_subnet_group" "main" {
  name       = "aidolab"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier                  = "aidolab-db"
  engine                      = "postgres"
  engine_version              = "17"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  max_allocated_storage       = 50
  storage_type                = "gp3"
  storage_encrypted           = true
  username                    = "aidolab_admin"
  manage_master_user_password = true # password lives in Secrets Manager, never in Terraform state
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 7
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "aidolab-db-final"
  auto_minor_version_upgrade  = true
  apply_immediately           = false
}

resource "aws_s3_bucket" "files" {
  bucket = "aidolab-files-${data.aws_caller_identity.me.account_id}"
}

resource "aws_s3_bucket_public_access_block" "files" {
  bucket                  = aws_s3_bucket.files.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "files" {
  bucket = aws_s3_bucket.files.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "files" {
  bucket = aws_s3_bucket.files.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Database dumps taken by the deploy worker before each migration (checker/aws.py backup_database).
# Separate from the files bucket: the app boundary policy allows the whole files bucket, never this one.
resource "aws_s3_bucket" "backups" {
  bucket = "aidolab-backups-${data.aws_caller_identity.me.account_id}"
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Keep in step with hosting.BACKUP_DAYS (how far back a rollback can restore the database).
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "expire-dumps"
    status = "Enabled"
    filter {}
    expiration { days = 30 }
  }
}
