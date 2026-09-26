locals {
  account_root = "arn:aws:iam::${var.account_id}:root"
  kms_all_use  = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]

  # DESIGN-IAM.md 3.3 (AlarmsAndBudgetsToSns carries aws:SourceAccount per PLAN 3.2).
  # Separate from local.policies: those reference the key ARN, so one map would be a cycle.
  key_policies = { "key/jarvis" = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountRootAdmin"
        Effect    = "Allow"
        Principal = { AWS = local.account_root }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogsUse"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action    = local.kms_all_use
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/jarvis/*"
          }
        }
      },
      {
        Sid       = "FlowLogDeliveryUse"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = local.kms_all_use
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = var.account_id }
          ArnLike      = { "aws:SourceArn" = "arn:aws:logs:${var.aws_region}:${var.account_id}:*" }
        }
      },
      {
        Sid       = "AlarmsAndBudgetsToSns"
        Effect    = "Allow"
        Principal = { Service = ["cloudwatch.amazonaws.com", "budgets.amazonaws.com"] }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = var.account_id }
        }
      },
      {
        Sid       = "BackupRoleUse"
        Effect    = "Allow"
        Principal = { AWS = var.backup_role_arn }
        Action    = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:ReEncrypt*"]
        Resource  = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = ["ec2.${var.aws_region}.amazonaws.com", "backup.${var.aws_region}.amazonaws.com"]
          }
        }
      },
      {
        Sid       = "BackupRoleGrants"
        Effect    = "Allow"
        Principal = { AWS = var.backup_role_arn }
        Action    = "kms:CreateGrant"
        Resource  = "*"
        Condition = { Bool = { "kms:GrantIsForAWSResource" = "true" } }
      },
    ]
  }) }
}

resource "aws_kms_key" "jarvis" {
  description             = "jarvis: EBS, secrets, logs, SNS, artifacts, flow logs, backup"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = local.key_policies["key/jarvis"]

  tags = { Name = "jarvis" }
}

resource "aws_kms_alias" "jarvis" {
  name          = "alias/jarvis"
  target_key_id = aws_kms_key.jarvis.key_id
}

resource "aws_ebs_encryption_by_default" "this" {
  enabled = true
}

resource "aws_ebs_default_kms_key" "this" {
  key_arn = aws_kms_key.jarvis.arn
}
