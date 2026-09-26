locals {
  bucket     = var.bucket_name
  bucket_arn = "arn:aws:s3:::${local.bucket}"
  objects    = "${local.bucket_arn}/*"

  admin_caller_re = "^arn:aws:sts::[0-9]{12}:assumed-role/(AWSReservedSSO_JarvisAdmin_[A-Za-z0-9]+|OrganizationAccountAccessRole)/"

  allowed_principals = [
    var.instance_role_arn,
    "${var.sso_role_arn_prefix}JarvisOperator_*",
    "${var.sso_role_arn_prefix}JarvisAdmin_*",
    "arn:aws:iam::${var.account_id}:role/OrganizationAccountAccessRole",
  ]

  bootstrap_key = "bootstrap/${data.archive_file.bootstrap.output_sha256}/bootstrap.tar.gz"

  # DESIGN-IAM.md 3.8 with the PLAN 3.2 DenyWrongKmsKey amendment (Null + StringNotEquals).
  policies = {
    "bucket/artifacts" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid       = "DenyInsecureTransport"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:*"
          Resource  = [local.bucket_arn, local.objects]
          Condition = { Bool = { "aws:SecureTransport" = "false" } }
        },
        {
          Sid       = "DenyUnlistedPrincipals"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:*"
          Resource  = [local.bucket_arn, local.objects]
          Condition = { ArnNotLike = { "aws:PrincipalArn" = local.allowed_principals } }
        },
        {
          Sid       = "DenyInstanceWrites"
          Effect    = "Deny"
          Principal = { AWS = var.instance_role_arn }
          Action    = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectAcl"]
          Resource  = local.objects
        },
        {
          Sid       = "DenyWrongKmsKey"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:PutObject"
          Resource  = local.objects
          Condition = {
            Null            = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = "false" }
            StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = var.kms_key_arn }
          }
        },
        {
          Sid       = "DenyNonKmsEncryption"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:PutObject"
          Resource  = local.objects
          Condition = {
            Null            = { "s3:x-amz-server-side-encryption" = "false" }
            StringNotEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" }
          }
        },
        {
          Sid       = "DenyKmsWithoutKeyId"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:PutObject"
          Resource  = local.objects
          Condition = {
            StringEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" }
            Null         = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = "true" }
          }
        },
      ]
    })
  }
}

# trivy:ignore:AVD-AWS-0089 Access logging would need another bucket; CloudTrail (org trail) records API access
resource "aws_s3_bucket" "artifacts" {
  bucket = local.bucket

  tags = { Name = local.bucket }
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  # Current objects (releases/, bootstrap/) are kept; only old versions expire.
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.artifacts]
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = local.policies["bucket/artifacts"]

  depends_on = [aws_s3_bucket_public_access_block.artifacts]

  # DenyUnlistedPrincipals would lock out any other caller, including the one applying this.
  lifecycle {
    precondition {
      condition     = can(regex(local.admin_caller_re, var.caller_arn))
      error_message = "Apply as the JarvisAdmin SSO role or OrganizationAccountAccessRole: the artifacts bucket policy denies every other principal, including ${var.caller_arn}."
    }
  }
}

# AD18: ops/aws packed by Terraform; the sha256 in the key pins the exact tarball.
data "archive_file" "bootstrap" {
  type        = "tar.gz"
  source_dir  = var.bootstrap_source_dir
  output_path = var.bootstrap_output_path
  # Defense in depth: nothing secret-shaped ever enters the tarball.
  excludes = [
    "**/__pycache__/**", "**/.DS_Store", "**/*.env", "**/.env*", "**/*.pem", "**/*token*",
  ]
}

resource "aws_s3_object" "bootstrap" {
  bucket                 = aws_s3_bucket.artifacts.id
  key                    = local.bootstrap_key
  source                 = data.archive_file.bootstrap.output_path
  source_hash            = data.archive_file.bootstrap.output_sha256
  content_type           = "application/gzip"
  server_side_encryption = "aws:kms"
  kms_key_id             = var.kms_key_arn

  depends_on = [
    aws_s3_bucket_policy.artifacts,
    aws_s3_bucket_server_side_encryption_configuration.artifacts,
  ]
}
