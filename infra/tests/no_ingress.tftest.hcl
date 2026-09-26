# No-ingress policy test (brief 9.1, DESIGN.md section 4 "Test harness rules").
#
# Fails on any aws_security_group ingress block, aws_security_group_rule (ingress or not; this
# repo uses only aws_vpc_security_group_egress_rule), aws_vpc_security_group_ingress_rule, an
# aws_default_security_group ingress, any aws_key_pair, key_name argument, aws_iam_user,
# aws_iam_access_key, or aws_secretsmanager_secret_version. The source scan covers every .tf file
# under bootstrap/, envs/, modules/, org/; the runtime checks read envs/prod's planned values.
#
# Run from infra/: `terraform init && terraform test`. Providers are mocked; nothing reaches AWS.

mock_provider "aws" {
  # ARN-shaped defaults: the provider validates ARNs even under a mock.
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-2:111122223333:key/00000000-0000-4000-8000-000000000000" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-2:111122223333:jarvis-alerts" }
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
    defaults = { arn = "arn:aws:secretsmanager:us-east-2:111122223333:secret:jarvis/mock-AbCdEf" }
  }
  mock_resource "aws_ssoadmin_permission_set" {
    defaults = { arn = "arn:aws:sso:::permissionSet/ssoins-test/ps-0000000000000000" }
  }
  mock_resource "aws_backup_vault" {
    defaults = { arn = "arn:aws:backup:us-east-2:111122223333:backup-vault:jarvis-backup" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0123456789abcdef0", latest_version = 1 }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-2:111122223333:log-group:/jarvis/mock" }
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

run "source_scan" {
  command = plan

  module {
    source = "./tests/modules/source_scan"
  }

  assert {
    condition     = length(output.files_scanned) > 20
    error_message = "The source scan found too few .tf files; check the working directory (run from infra/)."
  }

  assert {
    condition     = length(output.violations) == 0
    error_message = "Forbidden resources or arguments: ${jsonencode(output.violations)}"
  }

  # Gate 2 H1: exactly two security groups exist in all roots, so a new SG (with a static or
  # dynamic ingress block, or rules added later) cannot slip past the runtime checks below.
  assert {
    condition = jsonencode(output.security_group_resources) == jsonencode([
      "modules/network/main.tf:aws_default_security_group.default",
      "modules/network/main.tf:aws_security_group.instance",
    ])
    error_message = "Unexpected security group resources: ${jsonencode(output.security_group_resources)}"
  }
}

run "prod_security_groups" {
  command = apply

  module {
    source = "./envs/prod"
  }

  assert {
    condition     = length(output.security_groups.instance_sg_ingress) == 0
    error_message = "jarvis-instance-sg has ingress rules."
  }

  assert {
    condition     = length(output.security_groups.default_sg_ingress) == 0 && length(output.security_groups.default_sg_egress) == 0
    error_message = "The default security group must have no rules."
  }

  # DESIGN.md section 2, E1 to E6, and nothing else.
  assert {
    condition = output.security_groups.egress_rules == {
      https     = { proto = "tcp", from = 443, to = 443, cidr = "0.0.0.0/0" }
      http      = { proto = "tcp", from = 80, to = 80, cidr = "0.0.0.0/0" }
      wireguard = { proto = "udp", from = 41641, to = 41641, cidr = "0.0.0.0/0" }
      stun      = { proto = "udp", from = 3478, to = 3478, cidr = "0.0.0.0/0" }
      dns_udp   = { proto = "udp", from = 53, to = 53, cidr = "10.60.0.2/32" }
      dns_tcp   = { proto = "tcp", from = 53, to = 53, cidr = "10.60.0.2/32" }
    }
    error_message = "Egress rules differ from DESIGN.md section 2: ${jsonencode(output.security_groups.egress_rules)}"
  }

  assert {
    condition     = output.instance_settings.launch_template_key_name == null || output.instance_settings.launch_template_key_name == ""
    error_message = "The launch template must not set a key pair."
  }

  # aws_instance.key_name is computed, so a mock fills it randomly; the source scan (no key_name
  # argument anywhere) is the conclusive check for the instance.

  assert {
    condition     = output.instance_settings.http_tokens == "required" && output.instance_settings.hop_limit == 1
    error_message = "IMDSv2 with hop limit 1 is required."
  }
}

# Gate 2 L7: the first apply (AD30) creates no instance and no instance-scoped alarms, and the
# network still has zero ingress.
run "prod_instance_disabled" {
  command = apply

  module {
    source = "./envs/prod"
  }

  variables {
    instance_enabled = false
  }

  assert {
    condition     = output.instance_id == null
    error_message = "instance_enabled = false must not create the instance."
  }

  assert {
    condition     = length(output.security_groups.instance_sg_ingress) == 0 && length(output.security_groups.egress_rules) == 6
    error_message = "The network must be unchanged with the instance disabled."
  }
}
