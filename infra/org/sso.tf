# AD25: all aws_ssoadmin_* resources exist only when create_permission_sets = true.
data "aws_ssoadmin_instances" "this" {
  count = var.create_permission_sets ? 1 : 0
}

locals {
  sso_instance_arn = var.create_permission_sets ? tolist(data.aws_ssoadmin_instances.this[0].arns)[0] : null

  permission_sets = {
    JarvisClient   = { session = "PT1H", description = "Jarvis Windows client: read the two API token secrets" }
    JarvisOperator = { session = "PT4H", description = "Jarvis operator: SSM, releases, secret values (write only)" }
    JarvisAdmin    = { session = "PT1H", description = "Jarvis Terraform applies (bootstrap and envs/prod only)" }
  }

  inline_policy_sets = var.create_permission_sets ? toset(["JarvisClient", "JarvisOperator"]) : toset([])

  assignments = var.create_permission_sets ? {
    operator_operator = { set = "JarvisOperator", principal = var.operator_principal_id }
    operator_admin    = { set = "JarvisAdmin", principal = var.operator_principal_id }
    workstation       = { set = "JarvisClient", principal = var.workstation_principal_id }
  } : {}
}

resource "aws_ssoadmin_permission_set" "this" {
  for_each = var.create_permission_sets ? local.permission_sets : {}

  name             = each.key
  description      = each.value.description
  instance_arn     = local.sso_instance_arn
  session_duration = each.value.session
}

resource "aws_ssoadmin_permission_set_inline_policy" "this" {
  for_each = local.inline_policy_sets

  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.key].arn
  inline_policy      = local.policies["permission_set/${each.key}"]
}

resource "aws_ssoadmin_managed_policy_attachment" "admin" {
  count = var.create_permission_sets ? 1 : 0

  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this["JarvisAdmin"].arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_ssoadmin_account_assignment" "this" {
  for_each = local.assignments

  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.value.set].arn
  principal_id       = each.value.principal
  principal_type     = "USER"
  target_id          = local.account_id
  target_type        = "AWS_ACCOUNT"

  lifecycle {
    precondition {
      condition     = each.value.principal != null
      error_message = "create_permission_sets = true needs operator_principal_id and workstation_principal_id."
    }
  }
}
