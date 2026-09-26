output "alert_topic_arn" {
  description = "ARN of jarvis-alerts."
  value       = aws_sns_topic.alerts.arn
}

output "log_group_names" {
  description = "The four jarvis log groups (the instance role telemetry policy covers exactly these)."
  value       = [for g in local.log_groups : aws_cloudwatch_log_group.jarvis[g].name]
}

output "policies" {
  description = "Every policy document this module creates, keyed by policy name (read by infra/tests)."
  value       = local.policies
}

# Read by infra/tests (ofw.tftest.hcl): built from the resources, not the locals, so a test sees
# what Terraform would create.
output "inventory" {
  description = "Log groups, metric filters, and log alarms as created, keyed by name."
  value = {
    log_groups = { for k, g in aws_cloudwatch_log_group.jarvis : k => {
      retention_in_days = g.retention_in_days
      kms_key_id        = g.kms_key_id
    } }
    metric_filters = { for k, f in aws_cloudwatch_log_metric_filter.jarvis : k => {
      log_group     = f.log_group_name
      pattern       = f.pattern
      namespace     = f.metric_transformation[0].namespace
      metric        = f.metric_transformation[0].name
      value         = f.metric_transformation[0].value
      default_value = f.metric_transformation[0].default_value
      dimensions    = f.metric_transformation[0].dimensions
    } }
    log_alarms = { for k, a in aws_cloudwatch_metric_alarm.log : k => {
      namespace           = a.namespace
      metric              = a.metric_name
      statistic           = a.statistic
      period              = a.period
      evaluation_periods  = a.evaluation_periods
      datapoints_to_alarm = a.datapoints_to_alarm
      comparison_operator = a.comparison_operator
      threshold           = a.threshold
      treat_missing_data  = a.treat_missing_data
      dimensions          = a.dimensions
      alarm_actions       = a.alarm_actions
      ok_actions          = a.ok_actions
    } }
  }
}
