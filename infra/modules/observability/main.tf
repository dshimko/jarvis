locals {
  log_groups = ["/jarvis/work", "/jarvis/personal", "/jarvis/cloud-init", "/jarvis/ofw"]
  topic_arn  = aws_sns_topic.alerts.arn

  # DESIGN.md 7.2.
  metric_filters = {
    "jarvis-heartbeat-work" = {
      log_group  = "/jarvis/work"
      pattern    = "{ ($.event = \"heartbeat\") && ($.mode = \"work\") }"
      name       = "Heartbeat"
      default    = null
      dimensions = { Mode = "$.mode" }
    }
    "jarvis-heartbeat-personal" = {
      log_group  = "/jarvis/personal"
      pattern    = "{ ($.event = \"heartbeat\") && ($.mode = \"personal\") }"
      name       = "Heartbeat"
      default    = null
      dimensions = { Mode = "$.mode" }
    }
    "jarvis-ofw-watch-errors" = {
      log_group  = "/jarvis/personal"
      pattern    = "{ $.event = \"ofw_watch_error\" }"
      name       = "OfwWatchErrors"
      default    = "0"
      dimensions = null
    }
    "jarvis-ofw-watch-disabled" = {
      log_group  = "/jarvis/personal"
      pattern    = "{ $.event = \"ofw_watch_disabled\" }"
      name       = "OfwWatchDisabled"
      default    = "0"
      dimensions = null
    }
    # PLAN AD37: ofw-mcp events, shipped from /var/log/jarvis/ofw.jsonl.
    "jarvis-ofw-login-failures" = {
      log_group  = "/jarvis/ofw"
      pattern    = "{ $.event = \"ofw_login_failed\" }"
      name       = "OfwLoginFailures"
      default    = "0"
      dimensions = null
    }
    "jarvis-ofw-layout-changed" = {
      log_group  = "/jarvis/ofw"
      pattern    = "{ $.event = \"ofw_layout_changed\" }"
      name       = "OfwLayoutChanged"
      default    = "0"
      dimensions = null
    }
    "jarvis-heartbeat-ofw" = {
      log_group  = "/jarvis/ofw"
      pattern    = "{ ($.event = \"heartbeat\") && ($.mode = \"ofw\") }"
      name       = "Heartbeat"
      default    = null
      dimensions = { Mode = "$.mode" }
    }
  }
}

resource "aws_cloudwatch_log_group" "jarvis" {
  for_each = toset(local.log_groups)

  name              = each.key
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_cloudwatch_log_metric_filter" "jarvis" {
  for_each = local.metric_filters

  name           = each.key
  log_group_name = aws_cloudwatch_log_group.jarvis[each.value.log_group].name
  pattern        = each.value.pattern

  metric_transformation {
    namespace     = "Jarvis"
    name          = each.value.name
    value         = "1"
    unit          = "Count"
    default_value = each.value.default
    dimensions    = each.value.dimensions
  }
}
