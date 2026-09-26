locals {
  # DESIGN-IAM.md 3.11.
  policies = {
    "topic/jarvis-alerts" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid       = "OwnerManage"
          Effect    = "Allow"
          Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }
          Action = [
            "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:Subscribe",
            "sns:ListSubscriptionsByTopic", "sns:Publish", "sns:DeleteTopic",
          ]
          Resource = local.topic_arn
        },
        {
          Sid       = "CloudWatchAlarmsPublish"
          Effect    = "Allow"
          Principal = { Service = "cloudwatch.amazonaws.com" }
          Action    = "sns:Publish"
          Resource  = local.topic_arn
          Condition = {
            StringEquals = { "aws:SourceAccount" = var.account_id }
            ArnLike      = { "aws:SourceArn" = "arn:aws:cloudwatch:${var.aws_region}:${var.account_id}:alarm:jarvis-*" }
          }
        },
        {
          Sid       = "BudgetsPublish"
          Effect    = "Allow"
          Principal = { Service = "budgets.amazonaws.com" }
          Action    = "sns:Publish"
          Resource  = local.topic_arn
          Condition = {
            StringEquals = { "aws:SourceAccount" = var.account_id }
            ArnLike      = { "aws:SourceArn" = "arn:aws:budgets::${var.account_id}:*" }
          }
        },
        {
          Sid       = "DenyInsecurePublish"
          Effect    = "Deny"
          Principal = "*"
          Action    = "sns:Publish"
          Resource  = local.topic_arn
          Condition = { Bool = { "aws:SecureTransport" = "false" } }
        },
      ]
    })
  }
}

resource "aws_sns_topic" "alerts" {
  name              = "jarvis-alerts"
  kms_master_key_id = var.kms_key_arn
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = local.policies["topic/jarvis-alerts"]
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_budgets_budget" "monthly" {
  name         = "jarvis-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = toset(var.budget_thresholds_percent)

    content {
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value
      threshold_type            = "PERCENTAGE"
      notification_type         = "ACTUAL"
      subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
    }
  }

  depends_on = [aws_sns_topic_policy.alerts]
}
