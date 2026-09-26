locals {
  managed_policy_arns = {
    backup   = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
    restores = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
  }

  # DESIGN-IAM.md 3.10.
  policies = {
    "trust/jarvis-backup" = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid       = "BackupAssumeRole"
        Effect    = "Allow"
        Principal = { Service = "backup.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = { StringEquals = { "aws:SourceAccount" = var.account_id } }
      }]
    })
  }

  rules = {
    daily  = { schedule = var.backup_daily_schedule, delete_after = var.daily_retention_days }
    weekly = { schedule = var.backup_weekly_schedule, delete_after = var.weekly_retention_days }
  }
}

resource "aws_iam_role" "backup" {
  name               = "jarvis-backup"
  description        = "AWS Backup service role for the jarvis root volume"
  assume_role_policy = local.policies["trust/jarvis-backup"]
}

resource "aws_iam_role_policy_attachment" "backup" {
  for_each = local.managed_policy_arns

  role       = aws_iam_role.backup.name
  policy_arn = each.value
}

resource "aws_backup_vault" "jarvis" {
  name        = "jarvis-backup"
  kms_key_arn = var.kms_key_arn
}

resource "aws_backup_plan" "ebs" {
  name = "jarvis-ebs"

  dynamic "rule" {
    for_each = local.rules

    content {
      rule_name         = rule.key
      target_vault_name = aws_backup_vault.jarvis.name
      schedule          = rule.value.schedule
      start_window      = 60
      completion_window = 360

      lifecycle {
        delete_after = rule.value.delete_after
      }
    }
  }
}

resource "aws_backup_selection" "root_volume" {
  name         = "jarvis-root-volume"
  plan_id      = aws_backup_plan.ebs.id
  iam_role_arn = aws_iam_role.backup.arn
  resources    = ["arn:aws:ec2:${var.aws_region}:${var.account_id}:volume/*"]

  condition {
    string_equals {
      key   = "aws:ResourceTag/backup"
      value = "jarvis"
    }
  }
}
