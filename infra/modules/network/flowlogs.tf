locals {
  flowlogs_bucket     = "jarvis-flowlogs-${var.account_id}"
  flowlogs_bucket_arn = "arn:aws:s3:::${local.flowlogs_bucket}"
  logs_source_arn     = "arn:aws:logs:${var.aws_region}:${var.account_id}:*"

  # DESIGN-IAM.md 3.4. Every policy is a jsonencode() local (policy tests read them).
  policies = {
    "bucket/flowlogs" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid       = "AWSLogDeliveryWrite"
          Effect    = "Allow"
          Principal = { Service = "delivery.logs.amazonaws.com" }
          Action    = "s3:PutObject"
          Resource  = "${local.flowlogs_bucket_arn}/AWSLogs/${var.account_id}/*"
          Condition = {
            StringEquals = {
              "aws:SourceAccount" = var.account_id
              "s3:x-amz-acl"      = "bucket-owner-full-control"
            }
            ArnLike = { "aws:SourceArn" = local.logs_source_arn }
          }
        },
        {
          Sid       = "AWSLogDeliveryAclCheck"
          Effect    = "Allow"
          Principal = { Service = "delivery.logs.amazonaws.com" }
          Action    = ["s3:GetBucketAcl", "s3:ListBucket"]
          Resource  = local.flowlogs_bucket_arn
          Condition = {
            StringEquals = { "aws:SourceAccount" = var.account_id }
            ArnLike      = { "aws:SourceArn" = local.logs_source_arn }
          }
        },
        {
          Sid       = "DenyInsecureTransport"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:*"
          Resource  = [local.flowlogs_bucket_arn, "${local.flowlogs_bucket_arn}/*"]
          Condition = { Bool = { "aws:SecureTransport" = "false" } }
        },
      ]
    })
  }
}

# trivy:ignore:AVD-AWS-0089 This bucket is the log destination; access logging it would recurse
# trivy:ignore:AVD-AWS-0090 Flow log objects are write-once and expire at 90 days (DESIGN.md section 1)
resource "aws_s3_bucket" "flowlogs" {
  bucket = local.flowlogs_bucket

  tags = { Name = local.flowlogs_bucket }
}

resource "aws_s3_bucket_ownership_controls" "flowlogs" {
  bucket = aws_s3_bucket.flowlogs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "flowlogs" {
  bucket                  = aws_s3_bucket.flowlogs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "flowlogs" {
  bucket = aws_s3_bucket.flowlogs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "flowlogs" {
  bucket = aws_s3_bucket.flowlogs.id

  rule {
    id     = "expire-flow-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.flow_log_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "flowlogs" {
  bucket = aws_s3_bucket.flowlogs.id
  policy = local.policies["bucket/flowlogs"]

  depends_on = [aws_s3_bucket_public_access_block.flowlogs]
}
