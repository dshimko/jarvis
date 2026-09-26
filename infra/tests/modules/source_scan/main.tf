# Source-level guard over every .tf file under bootstrap/, envs/, modules/, org/ (run from
# infra/). It makes the no_ingress and IAM tests complete: a rule written anywhere, in any
# module, is caught even if no output exposes it.

locals {
  files = sort(fileset(var.root, "{bootstrap,envs,modules,org}/**/*.tf"))
  text  = { for f in local.files : f => file("${var.root}/${f}") }

  # Brief rule 0.5: the only resource types allowed (the Gate 2 enumeration). A new type,
  # e.g. aws_secretsmanager_secret_policy, aws_key_pair, or aws_iam_user, fails the test.
  allowed_resource_types = [
    "aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_route_table", "aws_route_table_association",
    "aws_security_group", "aws_vpc_security_group_egress_rule", "aws_default_security_group", "aws_flow_log",
    "aws_s3_bucket", "aws_s3_bucket_ownership_controls", "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_versioning", "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_lifecycle_configuration", "aws_s3_bucket_policy", "aws_s3_object",
    "aws_s3_account_public_access_block",
    "aws_kms_key", "aws_kms_alias", "aws_ebs_encryption_by_default", "aws_ebs_default_kms_key",
    "aws_iam_role", "aws_iam_role_policy", "aws_iam_role_policy_attachment", "aws_iam_instance_profile",
    "aws_network_interface", "aws_eip", "aws_launch_template", "aws_instance",
    "aws_cloudwatch_log_group", "aws_cloudwatch_log_metric_filter", "aws_cloudwatch_metric_alarm",
    "aws_sns_topic", "aws_sns_topic_policy", "aws_sns_topic_subscription", "aws_budgets_budget",
    "aws_secretsmanager_secret",
    "aws_backup_vault", "aws_backup_plan", "aws_backup_selection",
    "aws_ssm_document",
    "aws_organizations_organizational_unit", "aws_organizations_account", "aws_organizations_policy",
    "aws_organizations_policy_attachment",
    "aws_ssoadmin_permission_set", "aws_ssoadmin_permission_set_inline_policy",
    "aws_ssoadmin_managed_policy_attachment", "aws_ssoadmin_account_assignment",
    "tailscale_acl",
  ]
  allowed_data_types = [
    "aws_caller_identity", "aws_ami", "archive_file", "aws_organizations_organization", "aws_ssoadmin_instances",
  ]

  resource_type_hits = flatten([for f, t in local.text : [
    for m in regexall("(?m)^\\s*resource\\s+\"([a-z0-9_]+)\"", t) : "${f}: resource type ${m[0]} is not allowed (brief rule 0.5)"
    if !contains(local.allowed_resource_types, m[0])
  ]])
  data_type_hits = flatten([for f, t in local.text : [
    for m in regexall("(?m)^\\s*data\\s+\"([a-z0-9_]+)\"", t) : "${f}: data source ${m[0]} is not allowed"
    if !contains(local.allowed_data_types, m[0])
  ]])

  key_name_hits = [for f, t in local.text : "${f}: key_name argument" if length(regexall("(?m)^\\s*key_name\\s*=", t)) > 0]
  ingress_hits = [
    for f, t in local.text : "${f}: ingress block, dynamic block, or argument"
    if length(regexall("(?m)^\\s*(dynamic\\s+\"ingress\"|ingress\\s*(=|\\{))", t)) > 0
  ]
  inline_blk = [for f, t in local.text : "${f}: inline_policy block on a role" if length(regexall("(?m)^\\s*inline_policy\\s*\\{", t)) > 0]

  # Every policy-bearing argument must read a jsonencode() local map that the module outputs as
  # `policies`. The one exception: aws_ssm_document content in modules/ssm, which is a command
  # document, not a policy, and must come from local.documents.
  policy_attr_re = "(?m)^\\s*(policy|policy_document|assume_role_policy|inline_policy|content|key_policy)\\s*=\\s*(.+)$"
  policy_attr_hits = flatten([for f, t in local.text : [
    for m in regexall(local.policy_attr_re, t) : "${f}: ${m[0]} = ${m[1]} (must be local.policies[...] or local.key_policies[...])"
    if length(regexall("^local\\.(key_)?policies\\[", m[1])) == 0 &&
    !(startswith(f, "modules/ssm/") && m[0] == "content" && length(regexall("^local\\.documents\\[", m[1])) > 0)
  ]])

  # Every module and root with policy-bearing arguments exports them as `policies` whose value is
  # exactly local.policies or merge(local.key_policies, local.policies); never a literal.
  module_dirs        = distinct([for f in local.files : dirname(f)])
  dir_text           = { for d in local.module_dirs : d => join("\n", [for f, t in local.text : t if dirname(f) == d]) }
  policies_output_re = "output\\s+\"policies\"\\s*\\{[^}]*?value\\s*=\\s*([^\\n]+)"
  policies_output_ok = "^(local\\.policies|merge\\(local\\.key_policies, local\\.policies\\))\\s*$"

  modules_missing_output = [
    for d in local.module_dirs : "${d}: no `output \"policies\"`"
    if(startswith(d, "modules/") || length(regexall(local.policy_attr_re, local.dir_text[d])) > 0) &&
    length(regexall(local.policies_output_re, local.dir_text[d])) == 0
  ]

  # envs/prod only composes modules; its iam_policies output merges module outputs, so a policy
  # declared directly in the root would escape the tests.
  root_policy_hits = [
    for d in local.module_dirs : "${d}: policy-bearing argument in a composing root; put it in a module"
    if startswith(d, "envs/") && length(regexall(local.policy_attr_re, local.dir_text[d])) > 0
  ]

  bad_policies_output = flatten([for d in local.module_dirs : [
    for m in regexall(local.policies_output_re, local.dir_text[d]) : "${d}: output \"policies\" value is `${trimspace(m[0])}`, must be local.policies or merge(local.key_policies, local.policies)"
    if length(regexall(local.policies_output_ok, trimspace(m[0]))) == 0
  ]])

  managed_attachment_resources = sort(flatten([for f, t in local.text : [
    for m in regexall("resource\\s+\"(aws_iam_role_policy_attachment|aws_ssoadmin_managed_policy_attachment)\"\\s+\"([a-z0-9_]+)\"", t) : "${f}:${m[0]}.${m[1]}"
  ]]))

  # ofw.tftest.hcl inventory: every secret, log group, metric filter, and alarm resource block, so
  # a resource added outside the tested for_each maps cannot slip past the runtime checks.
  monitoring_resources = sort(flatten([for f, t in local.text : [
    for m in regexall("resource\\s+\"(aws_secretsmanager_secret|aws_cloudwatch_log_group|aws_cloudwatch_log_metric_filter|aws_cloudwatch_metric_alarm)\"\\s+\"([a-z0-9_]+)\"", t) : "${f}:${m[0]}.${m[1]}"
  ]]))

  security_group_resources = sort(flatten([for f, t in local.text : [
    for m in regexall("resource\\s+\"(aws_security_group|aws_default_security_group)\"\\s+\"([a-z0-9_]+)\"", t) : "${f}:${m[0]}.${m[1]}"
  ]]))
}
