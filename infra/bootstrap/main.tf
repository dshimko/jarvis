data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  bucket     = "jarvis-tfstate-${local.account_id}"
  bucket_arn = "arn:aws:s3:::${local.bucket}"
  objects    = "${local.bucket_arn}/*"

  admin_caller_re = "^arn:aws:sts::[0-9]{12}:assumed-role/(AWSReservedSSO_JarvisAdmin_[A-Za-z0-9]+|OrganizationAccountAccessRole)/"

  # <sso:Set> has no region segment: Identity Center is in us-east-1 (confirmed read-only).
  admins = [
    "arn:aws:iam::${local.account_id}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_JarvisAdmin_*",
    "arn:aws:iam::${local.account_id}:role/OrganizationAccountAccessRole",
  ]

  # DESIGN-IAM.md 3.3 (tfstate key) and 3.9, with the PLAN 3.2 DenyWrongKmsKey amendment.
  # The key policy is its own map: the bucket policy references the key ARN (no cycle).
  key_policies = {
    "key/jarvis-tfstate" = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid       = "AccountRootAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      }]
    })
  }

  policies = {
    "bucket/tfstate" = jsonencode({
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
          Condition = { ArnNotLike = { "aws:PrincipalArn" = local.admins } }
        },
        {
          Sid       = "DenyWrongKmsKey"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:PutObject"
          Resource  = local.objects
          Condition = {
            Null            = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = "false" }
            StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = aws_kms_key.tfstate.arn }
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
        {
          Sid       = "DenyBucketDelete"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:DeleteBucket"
          Resource  = local.bucket_arn
        },
      ]
    })
  }
}

resource "aws_kms_key" "tfstate" {
  description             = "jarvis: Terraform state bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = local.key_policies["key/jarvis-tfstate"]

  tags = { Name = "jarvis-tfstate" }
}

resource "aws_kms_alias" "tfstate" {
  name          = "alias/jarvis-tfstate"
  target_key_id = aws_kms_key.tfstate.key_id
}

# trivy:ignore:AVD-AWS-0089 Access logging would need a second bucket; the org CloudTrail trail records API access
resource "aws_s3_bucket" "tfstate" {
  bucket = local.bucket

  tags = { Name = local.bucket }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.tfstate.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

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

  depends_on = [aws_s3_bucket_versioning.tfstate]
}

resource "aws_s3_bucket_policy" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  policy = local.policies["bucket/tfstate"]

  depends_on = [aws_s3_bucket_public_access_block.tfstate]

  # DenyUnlistedPrincipals would lock out any other caller, including the one applying this.
  lifecycle {
    precondition {
      condition     = can(regex(local.admin_caller_re, data.aws_caller_identity.current.arn))
      error_message = "Apply as the JarvisAdmin SSO role or OrganizationAccountAccessRole: the state bucket policy denies every other principal, including ${data.aws_caller_identity.current.arn}."
    }
  }
}

resource "aws_s3_account_public_access_block" "this" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
