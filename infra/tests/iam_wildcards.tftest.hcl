# IAM wildcard policy test (brief 9.1, DESIGN.md section 4, PLAN 3.2 "Policy test precision").
#
# Every policy in envs/prod, bootstrap, and org (with create_permission_sets = true) is decoded
# from its jsonencode() output. Any statement with `*` or `?` in Resource/NotResource must match
# its allowlist row exactly (effect, actions, resources, principals, and every condition as
# operator, key, exact values). The allowlist is DESIGN.md section 4, encoded as locals in
# tests/modules/policy_check/allowlist_*.tf (a .tftest.hcl file cannot declare locals).
# Allow with Action "*" or Principal "*" fails. All 38 rows must be matched (no stale rows).
# Managed attachments must be exactly DESIGN.md 4.3.
#
# Run from infra/: `terraform init && terraform test`. Providers are mocked; nothing reaches AWS.
# Fixed identities come from overrides: ACCT 111122223333, the key ARNs, role ARNs, and EIP below.

mock_provider "aws" {
  # ARN-shaped defaults: the provider validates ARNs even under a mock.
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-4000-8000-000000000000" }
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
  mock_resource "aws_ssoadmin_permission_set" {
    defaults = { arn = "arn:aws:sso:::permissionSet/ssoins-test/ps-0000000000000000" }
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
  alert_email              = "alerts@example.com"
  expected_account_id      = "111122223333"
  account_email            = "jarvis-prod@example.com"
  create_permission_sets   = true
  attach_region_scp        = true
  attach_guardrail_scp     = true
  operator_principal_id    = "11111111-aaaa-bbbb-cccc-000000000001"
  workstation_principal_id = "11111111-aaaa-bbbb-cccc-000000000002"

  # DESIGN.md 4.3: the only AWS managed policy attachments.
  expected_managed_attachments = [
    "jarvis-instance|arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "jarvis-backup|arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup",
    "jarvis-backup|arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores",
    "JarvisAdmin|arn:aws:iam::aws:policy/AdministratorAccess",
  ]
}

run "prod" {
  command = apply

  module {
    source = "./envs/prod"
  }

  override_resource {
    target = module.compute.aws_kms_key.jarvis
    values = {
      arn    = "arn:aws:kms:us-east-1:111122223333:key/11111111-1111-4111-8111-111111111111"
      key_id = "11111111-1111-4111-8111-111111111111"
    }
  }

  override_resource {
    target = module.compute.aws_eip.jarvis
    values = { public_ip = "203.0.113.10" }
  }

  override_resource {
    target = module.compute.aws_iam_role.instance
    values = { arn = "arn:aws:iam::111122223333:role/jarvis-instance" }
  }

  override_resource {
    target = module.backup.aws_iam_role.backup
    values = { arn = "arn:aws:iam::111122223333:role/jarvis-backup" }
  }

  override_resource {
    target = module.observability.aws_sns_topic.alerts
    values = { arn = "arn:aws:sns:us-east-1:111122223333:jarvis-alerts" }
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

  # PLAN AD34: X3/X4 in check_prod pin the five value ARNs (ofw.tftest.hcl asserts them too).
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

  assert {
    condition     = output.iam_policy_count == length(output.iam_policies)
    error_message = "Two modules emit the same policy name; iam_policies would hide one."
  }
}

run "bootstrap" {
  command = apply

  module {
    source = "./bootstrap"
  }

  override_resource {
    target = aws_kms_key.tfstate
    values = {
      arn    = "arn:aws:kms:us-east-1:111122223333:key/22222222-2222-4222-8222-222222222222"
      key_id = "22222222-2222-4222-8222-222222222222"
    }
  }
}

run "org" {
  command = apply

  module {
    source = "./org"
  }

  override_data {
    target = data.aws_organizations_organization.this
    values = {
      roots = [{
        id           = "r-test"
        arn          = "arn:aws:organizations::080109295043:root/o-test/r-test"
        name         = "Root"
        policy_types = [{ type = "SERVICE_CONTROL_POLICY", status = "ENABLED" }]
      }]
    }
  }

  override_data {
    target = data.aws_ssoadmin_instances.this
    values = {
      arns               = ["arn:aws:sso:::instance/ssoins-test"]
      identity_store_ids = ["d-test"]
    }
  }

  override_resource {
    target = aws_organizations_organizational_unit.jarvis
    values = { id = "ou-test-11111111" }
  }

  override_resource {
    target = aws_organizations_account.jarvis_prod
    values = { id = "111122223333" }
  }

  assert {
    condition     = length(output.permission_set_arns) == 3
    error_message = "create_permission_sets = true must create the three permission sets."
  }

  assert {
    condition     = jsonencode(output.attached_scps) == jsonencode(["jarvis-guardrail", "jarvis-org-guard", "jarvis-region-deny"])
    error_message = "With both flags on, all three SCPs must be attached."
  }

  assert {
    condition     = aws_organizations_account.jarvis_prod.parent_id == output.ou_id && aws_organizations_account.jarvis_prod.close_on_deletion
    error_message = "AD43: jarvis-prod must sit in the jarvis OU with close_on_deletion = true."
  }

  assert {
    condition     = alltrue([for a in aws_organizations_policy_attachment.scp : a.target_id == output.ou_id])
    error_message = "AD43: every SCP must be attached to the jarvis OU, not the account."
  }
}

run "check_prod" {
  command = plan

  module {
    source = "./tests/modules/policy_check"
  }

  variables {
    policies = run.prod.iam_policies
  }

  assert {
    condition     = length(output.violations) == 0
    error_message = "envs/prod policy violations: ${jsonencode(output.violations)}"
  }
}

run "check_bootstrap" {
  command = plan

  module {
    source = "./tests/modules/policy_check"
  }

  variables {
    policies = run.bootstrap.policies
  }

  assert {
    condition     = length(output.violations) == 0
    error_message = "bootstrap policy violations: ${jsonencode(output.violations)}"
  }
}

run "check_org" {
  command = plan

  module {
    source = "./tests/modules/policy_check"
  }

  variables {
    policies = run.org.policies
  }

  assert {
    condition     = length(output.violations) == 0
    error_message = "org policy violations: ${jsonencode(output.violations)}"
  }

  # All 38 rows are matched by some statement: none is stale, none is silently unused.
  assert {
    condition = (
      toset(concat(run.check_prod.matched_keys, run.check_bootstrap.matched_keys, output.matched_keys)) == toset(output.allowlist_keys)
    )
    error_message = "Allowlist rows not matched by any policy: ${jsonencode(setsubtract(output.allowlist_keys, concat(run.check_prod.matched_keys, run.check_bootstrap.matched_keys, output.matched_keys)))}"
  }

  assert {
    condition     = length(output.allowlist_keys) == 38 && length(concat(run.check_prod.matched_keys, run.check_bootstrap.matched_keys, output.matched_keys)) == 38
    error_message = "Expected exactly 38 wildcard statements (16 Resource \"*\", 22 patterns); a row matched twice or is missing."
  }

  # Every non-wildcard statement row is used exactly once (17 rows, exact_statements.tf).
  assert {
    condition = (
      toset(concat(run.check_prod.matched_exact_keys, run.check_bootstrap.matched_exact_keys, output.matched_exact_keys)) == toset(output.exact_keys) &&
      length(concat(run.check_prod.matched_exact_keys, run.check_bootstrap.matched_exact_keys, output.matched_exact_keys)) == length(output.exact_keys)
    )
    error_message = "Exact-match rows not matched exactly once: ${jsonencode(setsubtract(output.exact_keys, concat(run.check_prod.matched_exact_keys, run.check_bootstrap.matched_exact_keys, output.matched_exact_keys)))}"
  }

  # DESIGN.md 4.3: exactly this managed-policy set.
  assert {
    condition = toset([
      for a in concat(run.prod.managed_policy_attachments, run.org.managed_policy_attachments) : "${a.attached_to}|${a.policy_arn}"
    ]) == toset(var.expected_managed_attachments) && length(concat(run.prod.managed_policy_attachments, run.org.managed_policy_attachments)) == 4
    error_message = "Managed policy attachments differ from DESIGN.md 4.3."
  }
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
    error_message = "Source violations (policies must be jsonencode() locals): ${jsonencode(output.violations)}"
  }

  assert {
    condition = jsonencode(output.managed_attachment_resources) == jsonencode([
      "modules/backup/main.tf:aws_iam_role_policy_attachment.backup",
      "modules/compute/iam.tf:aws_iam_role_policy_attachment.ssm_core",
      "org/sso.tf:aws_ssoadmin_managed_policy_attachment.admin",
    ])
    error_message = "Unexpected managed-policy attachment resources: ${jsonencode(output.managed_attachment_resources)}"
  }
}

# org/ must refuse to attach any SCP when the SCP policy type is not enabled (DESIGN.md 1).
run "org_scp_type_disabled" {
  command = plan

  module {
    source = "./org"
  }

  variables {
    create_permission_sets = false
  }

  override_data {
    target = data.aws_organizations_organization.this
    values = {
      roots = [{
        id           = "r-test"
        arn          = "arn:aws:organizations::080109295043:root/o-test/r-test"
        name         = "Root"
        policy_types = []
      }]
    }
  }

  override_resource {
    target = aws_organizations_organizational_unit.jarvis
    values = { id = "ou-test-11111111" }
  }

  override_resource {
    target = aws_organizations_account.jarvis_prod
    values = { id = "111122223333" }
  }

  expect_failures = [aws_organizations_policy_attachment.scp]
}

# Gate 2 M2: a caller outside JarvisAdmin / OrganizationAccountAccessRole fails the plan before
# a DenyUnlistedPrincipals bucket policy can lock it out.
run "bootstrap_wrong_caller" {
  command = plan

  module {
    source = "./bootstrap"
  }

  override_data {
    target = data.aws_caller_identity.current
    values = {
      account_id = "111122223333"
      arn        = "arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_JarvisOperator_0123456789abcdef/test"
      user_id    = "AROATEST:test"
    }
  }

  expect_failures = [aws_s3_bucket_policy.tfstate]
}

run "artifacts_wrong_caller" {
  command = plan

  module {
    source = "./modules/artifacts"
  }

  variables {
    account_id            = "111122223333"
    bucket_name           = "jarvis-artifacts-111122223333"
    sso_role_arn_prefix   = "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_"
    caller_arn            = "arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_JarvisOperator_0123456789abcdef/test"
    kms_key_arn           = "arn:aws:kms:us-east-1:111122223333:key/11111111-1111-4111-8111-111111111111"
    instance_role_arn     = "arn:aws:iam::111122223333:role/jarvis-instance"
    bootstrap_source_dir  = "../ops/aws"
    bootstrap_output_path = "envs/prod/.build/bootstrap.tar.gz"
  }

  expect_failures = [aws_s3_bucket_policy.artifacts]
}
