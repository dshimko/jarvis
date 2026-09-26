output "alert_topic_arn" {
  description = "ARN of jarvis-alerts."
  value       = aws_sns_topic.alerts.arn
}

output "log_group_names" {
  description = "The three jarvis log groups."
  value       = [for g in local.log_groups : aws_cloudwatch_log_group.jarvis[g].name]
}

output "policies" {
  description = "Every policy document this module creates, keyed by policy name (read by infra/tests)."
  value       = local.policies
}
