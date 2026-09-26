locals {
  # DESIGN.md 7.3. Every alarm notifies jarvis-alerts on ALARM and OK.
  log_alarms = {
    "jarvis-heartbeat-work" = {
      metric  = "Heartbeat", stat = "SampleCount", eval = 3, op = "LessThanThreshold", threshold = 1
      missing = "breaching", dimensions = { Mode = "work" }, filter = "jarvis-heartbeat-work"
    }
    "jarvis-heartbeat-personal" = {
      metric  = "Heartbeat", stat = "SampleCount", eval = 3, op = "LessThanThreshold", threshold = 1
      missing = "breaching", dimensions = { Mode = "personal" }, filter = "jarvis-heartbeat-personal"
    }
    "jarvis-ofw-watch-errors" = {
      metric  = "OfwWatchErrors", stat = "Sum", eval = 3, op = "GreaterThanThreshold", threshold = 0
      missing = "notBreaching", dimensions = null, filter = "jarvis-ofw-watch-errors"
    }
    "jarvis-ofw-watch-disabled" = {
      metric  = "OfwWatchDisabled", stat = "Sum", eval = 1, op = "GreaterThanThreshold", threshold = 0
      missing = "notBreaching", dimensions = null, filter = "jarvis-ofw-watch-disabled"
    }
  }

  instance_count = var.instance_enabled ? 1 : 0
}

resource "aws_cloudwatch_metric_alarm" "log" {
  for_each = local.log_alarms

  alarm_name          = each.key
  namespace           = "Jarvis"
  metric_name         = each.value.metric
  statistic           = each.value.stat
  period              = 300
  evaluation_periods  = each.value.eval
  datapoints_to_alarm = each.value.eval
  comparison_operator = each.value.op
  threshold           = each.value.threshold
  treat_missing_data  = each.value.missing
  dimensions          = each.value.dimensions
  alarm_actions       = [local.topic_arn]
  ok_actions          = [local.topic_arn]

  depends_on = [aws_cloudwatch_log_metric_filter.jarvis]
}

resource "aws_cloudwatch_metric_alarm" "disk" {
  count = local.instance_count

  alarm_name          = "jarvis-disk-used"
  alarm_description   = "Root volume above 80 percent (CloudWatch agent disk_used_percent)"
  namespace           = "Jarvis"
  metric_name         = "disk_used_percent"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  treat_missing_data  = "missing"
  dimensions          = { InstanceId = var.instance_id, path = "/", fstype = "ext4" }
  alarm_actions       = [local.topic_arn]
  ok_actions          = [local.topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "status_instance" {
  count = local.instance_count

  alarm_name          = "jarvis-status-instance"
  alarm_description   = "EC2 instance status check failed"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "missing"
  dimensions          = { InstanceId = var.instance_id }
  alarm_actions       = [local.topic_arn]
  ok_actions          = [local.topic_arn]
}
