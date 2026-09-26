# OFW MCP server resources (PLAN AD34, AD37, AD40).
#
# - jarvis/ofw exists as an empty value secret (the source scan in no_ingress/iam_wildcards
#   already fails on any aws_secretsmanager_secret_version anywhere).
# - /jarvis/ofw, its three metric filters, and their three alarms exist with the AD37 shape.
# - Inventory asserts pin every secret, log group, filter, and alarm resource block and every
#   for_each key, so a runtime check cannot silently miss a resource (CLAUDE.md pitfall).
# - The instance role's secret lists are exactly five value ARNs and two token ARNs.
#
# Run from infra/: `terraform init && terraform test`. Providers are mocked; nothing reaches AWS.

mock_provider "aws" {
  # ARN-shaped defaults: the provider validates ARNs even under a mock.
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:111122223333:key/11111111-1111-4111-8111-111111111111" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:111122223333:jarvis-alerts" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::111122223333:instance-profile/jarvis-instance" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-bucket" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/mock-AbCdEf" }
  }
  mock_resource "aws_backup_vault" {
    defaults = { arn = "arn:aws:backup:us-east-1:111122223333:backup-vault:jarvis-backup" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0123456789abcdef0", latest_version = 1 }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:111122223333:log-group:/jarvis/mock" }
  }

  override_data {
    target = data.aws_caller_identity.current
    values = {
      account_id = "111122223333"
      arn        = "arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_JarvisAdmin_0123456789abcdef/test"
      user_id    = "AROATEST:test"
    }
  }
}

mock_provider "archive" {}
mock_provider "tailscale" {}

variables {
  alert_email         = "alerts@example.com"
  expected_account_id = "111122223333"
}

run "ofw_source_inventory" {
  command = plan

  module {
    source = "./tests/modules/source_scan"
  }

  assert {
    condition = jsonencode(output.monitoring_resources) == jsonencode([
      "modules/compute/instance.tf:aws_cloudwatch_metric_alarm.auto_recover",
      "modules/observability/alarms.tf:aws_cloudwatch_metric_alarm.disk",
      "modules/observability/alarms.tf:aws_cloudwatch_metric_alarm.log",
      "modules/observability/alarms.tf:aws_cloudwatch_metric_alarm.status_instance",
      "modules/observability/main.tf:aws_cloudwatch_log_group.jarvis",
      "modules/observability/main.tf:aws_cloudwatch_log_metric_filter.jarvis",
      "modules/secrets/main.tf:aws_secretsmanager_secret.token",
      "modules/secrets/main.tf:aws_secretsmanager_secret.value",
    ])
    error_message = "Unexpected secret, log group, filter, or alarm resources: ${jsonencode(output.monitoring_resources)}"
  }
}

# PLAN AD34 (DESIGN-IAM.md 3.2 "AD34 deviation"): the instance role reads and decrypts exactly
# the five value secrets, jarvis/ofw included, and writes exactly the two token secrets. The
# full statements are pinned by X3 to X6 in iam_wildcards.tftest.hcl run "check_prod".
run "ofw_instance_secrets_policy" {
  command = apply

  module {
    source = "./envs/prod"
  }

  override_resource {
    target = module.secrets.aws_secretsmanager_secret.value["work"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/work-AAAAAA" }
  }
  override_resource {
    target = module.secrets.aws_secretsmanager_secret.value["personal"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/personal-BBBBBB" }
  }
  override_resource {
    target = module.secrets.aws_secretsmanager_secret.value["shared"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/shared-CCCCCC" }
  }
  override_resource {
    target = module.secrets.aws_secretsmanager_secret.value["tailscale"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/tailscale-DDDDDD" }
  }
  override_resource {
    target = module.secrets.aws_secretsmanager_secret.value["ofw"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/ofw-GGGGGG" }
  }
  override_resource {
    target = module.secrets.aws_secretsmanager_secret.token["work"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/work/api-token-EEEEEE" }
  }
  override_resource {
    target = module.secrets.aws_secretsmanager_secret.token["personal"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/personal/api-token-FFFFFF" }
  }

  # Per Sid, the secret ARNs it covers (Resource, or the kms:EncryptionContext:SecretARN pin).
  # Every statement except DenyOffInstance is listed, so a new secrets statement also fails.
  assert {
    condition = jsonencode({
      for st in jsondecode(output.iam_policies["instance/jarvis-secrets"]).Statement :
      st.Sid => sort(try(st.Condition.StringEquals["kms:EncryptionContext:SecretARN"], st.Resource))
      if st.Sid != "DenyOffInstance"
      }) == jsonencode({
      for sid in ["ReadModeSecrets", "DecryptModeSecrets", "PublishApiTokens", "EncryptApiTokens"] : sid => sort(
        endswith(sid, "ModeSecrets")
        ? [for s in ["work-AAAAAA", "personal-BBBBBB", "shared-CCCCCC", "tailscale-DDDDDD", "ofw-GGGGGG"] : "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/${s}"]
        : [for s in ["work/api-token-EEEEEE", "personal/api-token-FFFFFF"] : "arn:aws:secretsmanager:us-east-1:111122223333:secret:jarvis/${s}"]
      )
    })
    error_message = "jarvis-secrets must cover exactly the five value ARNs (work, personal, shared, tailscale, ofw) for read and decrypt, and the two token ARNs for write."
  }
}

run "ofw_prod_resources" {
  command = apply

  module {
    source = "./envs/prod"
  }

  # AD34: seven secrets, jarvis/ofw among them.
  assert {
    condition = jsonencode(sort(output.secret_names)) == jsonencode(sort([
      "jarvis/work", "jarvis/personal", "jarvis/shared", "jarvis/tailscale", "jarvis/ofw",
      "jarvis/work/api-token", "jarvis/personal/api-token",
    ]))
    error_message = "Secrets differ from the seven of PLAN AD34: ${jsonencode(output.secret_names)}"
  }

  # AD37: exactly these log groups; /jarvis/ofw has 30 days and the jarvis key.
  assert {
    condition = jsonencode(sort(keys(output.observability_inventory.log_groups))) == jsonencode(sort([
      "/jarvis/work", "/jarvis/personal", "/jarvis/cloud-init", "/jarvis/ofw",
    ]))
    error_message = "Log groups differ: ${jsonencode(keys(output.observability_inventory.log_groups))}"
  }

  assert {
    condition = output.observability_inventory.log_groups["/jarvis/ofw"] == {
      retention_in_days = 30
      kms_key_id        = "arn:aws:kms:us-east-1:111122223333:key/11111111-1111-4111-8111-111111111111"
    }
    error_message = "/jarvis/ofw must have 30-day retention and the jarvis KMS key."
  }

  # The telemetry policy is built from log_group_names, so the instance may write /jarvis/ofw.
  assert {
    condition = contains(one([
      for st in jsondecode(output.iam_policies["instance/jarvis-telemetry"]).Statement : st.Resource if st.Sid == "WriteJarvisLogs"
    ]), "arn:aws:logs:us-east-1:111122223333:log-group:/jarvis/ofw:*")
    error_message = "WriteJarvisLogs must cover /jarvis/ofw."
  }

  # Filter inventory: every for_each key, then the three AD37 filters field by field.
  assert {
    condition = jsonencode(sort(keys(output.observability_inventory.metric_filters))) == jsonencode(sort([
      "jarvis-heartbeat-work", "jarvis-heartbeat-personal", "jarvis-ofw-watch-errors", "jarvis-ofw-watch-disabled",
      "jarvis-ofw-login-failures", "jarvis-ofw-layout-changed", "jarvis-heartbeat-ofw",
    ]))
    error_message = "Metric filters differ: ${jsonencode(keys(output.observability_inventory.metric_filters))}"
  }

  assert {
    condition = jsonencode({
      for k in ["jarvis-ofw-login-failures", "jarvis-ofw-layout-changed", "jarvis-heartbeat-ofw"] :
      k => output.observability_inventory.metric_filters[k]
      }) == jsonencode({
      "jarvis-ofw-login-failures" = {
        log_group = "/jarvis/ofw", pattern = "{ $.event = \"ofw_login_failed\" }", namespace = "Jarvis"
        metric    = "OfwLoginFailures", value = "1", default_value = "0", dimensions = null
      }
      "jarvis-ofw-layout-changed" = {
        log_group = "/jarvis/ofw", pattern = "{ $.event = \"ofw_layout_changed\" }", namespace = "Jarvis"
        metric    = "OfwLayoutChanged", value = "1", default_value = "0", dimensions = null
      }
      "jarvis-heartbeat-ofw" = {
        log_group = "/jarvis/ofw", pattern = "{ ($.event = \"heartbeat\") && ($.mode = \"ofw\") }", namespace = "Jarvis"
        metric    = "Heartbeat", value = "1", default_value = null, dimensions = { Mode = "$.mode" }
      }
    })
    error_message = "ofw metric filters differ from PLAN AD37: ${jsonencode({ for k, v in output.observability_inventory.metric_filters : k => v if startswith(k, "jarvis-ofw-") || k == "jarvis-heartbeat-ofw" })}"
  }

  # Alarm inventory: every for_each key, then the three AD37 alarms field by field.
  assert {
    condition = jsonencode(sort(keys(output.observability_inventory.log_alarms))) == jsonencode(sort([
      "jarvis-heartbeat-work", "jarvis-heartbeat-personal", "jarvis-ofw-watch-errors", "jarvis-ofw-watch-disabled",
      "jarvis-ofw-login-failures", "jarvis-ofw-layout-changed", "jarvis-heartbeat-ofw",
    ]))
    error_message = "Log alarms differ: ${jsonencode(keys(output.observability_inventory.log_alarms))}"
  }

  # The ofw heartbeat alarm is the work heartbeat alarm with Mode = ofw.
  assert {
    condition = jsonencode(output.observability_inventory.log_alarms["jarvis-heartbeat-ofw"]) == jsonencode(merge(
      output.observability_inventory.log_alarms["jarvis-heartbeat-work"], { dimensions = { Mode = "ofw" } }
    ))
    error_message = "jarvis-heartbeat-ofw must match jarvis-heartbeat-work except Mode = ofw."
  }

  assert {
    condition = jsonencode({
      for k in ["jarvis-ofw-login-failures", "jarvis-ofw-layout-changed", "jarvis-heartbeat-ofw"] :
      k => output.observability_inventory.log_alarms[k]
      }) == jsonencode({
      for k, m in {
        "jarvis-ofw-login-failures" = { metric = "OfwLoginFailures", statistic = "Sum", eval = 1, op = "GreaterThanThreshold", threshold = 0, missing = "notBreaching", dimensions = null }
        "jarvis-ofw-layout-changed" = { metric = "OfwLayoutChanged", statistic = "Sum", eval = 1, op = "GreaterThanThreshold", threshold = 0, missing = "notBreaching", dimensions = null }
        "jarvis-heartbeat-ofw"      = { metric = "Heartbeat", statistic = "SampleCount", eval = 3, op = "LessThanThreshold", threshold = 1, missing = "breaching", dimensions = { Mode = "ofw" } }
        } : k => {
        namespace           = "Jarvis"
        metric              = m.metric
        statistic           = m.statistic
        period              = 300
        evaluation_periods  = m.eval
        datapoints_to_alarm = m.eval
        comparison_operator = m.op
        threshold           = m.threshold
        treat_missing_data  = m.missing
        dimensions          = m.dimensions
        alarm_actions       = ["arn:aws:sns:us-east-1:111122223333:jarvis-alerts"]
        ok_actions          = ["arn:aws:sns:us-east-1:111122223333:jarvis-alerts"]
      }
    })
    error_message = "ofw alarms differ from PLAN AD37: ${jsonencode({ for k, v in output.observability_inventory.log_alarms : k => v if startswith(k, "jarvis-ofw-l") || k == "jarvis-heartbeat-ofw" })}"
  }

  # AD40: six command documents.
  assert {
    condition = jsonencode(output.ssm_documents) == jsonencode(sort([
      "jarvis-deploy", "jarvis-restart", "jarvis-secrets-sync", "jarvis-status", "jarvis-ofw-login", "jarvis-ofw-reset",
    ]))
    error_message = "SSM command documents differ: ${jsonencode(output.ssm_documents)}"
  }
}

# Gate I MEDIUM: SSM splices each {{ Param }} into a root shell script, so the parameter
# constraints are the barrier between an SSM caller and root. For all six Command documents,
# pin the exact parameters block (name, type, description, default, allowedPattern or
# allowedValues), the single step, its timeoutSeconds, and the exported parameter lines.
run "ssm_document_parameters" {
  command = apply

  module {
    source = "./modules/ssm"
  }

  variables {
    documents = {
      for d in ["jarvis-deploy", "jarvis-restart", "jarvis-secrets-sync", "jarvis-status", "jarvis-ofw-login", "jarvis-ofw-reset"] :
      d => "#!/usr/bin/env bash\necho body\n"
    }
  }

  assert {
    condition = jsonencode({
      for k, d in aws_ssm_document.command : k => {
        parameters = try(jsondecode(d.content).parameters, {})
        steps      = length(jsondecode(d.content).mainSteps)
        action     = jsondecode(d.content).mainSteps[0].action
        timeout    = jsondecode(d.content).mainSteps[0].inputs.timeoutSeconds
        run        = jsondecode(d.content).mainSteps[0].inputs.runCommand
      }
      }) == jsonencode({
      "jarvis-deploy" = {
        parameters = { Sha = { type = "String", description = "40-character git sha", allowedPattern = "^[0-9a-f]{40}$" } }
        steps      = 1, action = "aws:runShellScript", timeout = "1800"
        run        = ["#!/usr/bin/env bash", "export SHA='{{ Sha }}'", "echo body"]
      }
      "jarvis-restart" = {
        parameters = {
          Mode   = { type = "String", description = "Mode to restart", allowedValues = ["work", "personal"] }
          Rotate = { type = "String", description = "Rotate the API token first", allowedValues = ["false", "true"], default = "false" }
        }
        steps = 1, action = "aws:runShellScript", timeout = "300"
        run   = ["#!/usr/bin/env bash", "export MODE='{{ Mode }}'", "export ROTATE='{{ Rotate }}'", "echo body"]
      }
      "jarvis-secrets-sync" = {
        parameters = {}, steps = 1, action = "aws:runShellScript", timeout = "300"
        run        = ["#!/usr/bin/env bash", "echo body"]
      }
      "jarvis-status" = {
        parameters = {}, steps = 1, action = "aws:runShellScript", timeout = "300"
        run        = ["#!/usr/bin/env bash", "echo body"]
      }
      "jarvis-ofw-login" = {
        parameters = { WaitSeconds = { type = "String", description = "Seconds to wait for the human to log in", allowedPattern = "^[0-9]{1,4}$", default = "900" } }
        steps      = 1, action = "aws:runShellScript", timeout = "10800"
        run        = ["#!/usr/bin/env bash", "export WAIT_SECONDS='{{ WaitSeconds }}'", "echo body"]
      }
      "jarvis-ofw-reset" = {
        parameters = {}, steps = 1, action = "aws:runShellScript", timeout = "300"
        run        = ["#!/usr/bin/env bash", "echo body"]
      }
    })
    error_message = "SSM Command documents differ from the pinned parameters/timeouts: ${jsonencode({ for k, d in aws_ssm_document.command : k => { parameters = try(jsondecode(d.content).parameters, {}), timeout = jsondecode(d.content).mainSteps[0].inputs.timeoutSeconds } })}"
  }
}
