data "aws_caller_identity" "current" {}

locals {
  account_id       = data.aws_caller_identity.current.account_id
  artifacts_bucket = "jarvis-artifacts-${local.account_id}"
  # <sso:Set> prefix. No region segment: Identity Center is in us-east-1 (confirmed read-only).
  sso_role_arn_prefix = "arn:aws:iam::${local.account_id}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_"
  repo_root           = abspath("${path.module}/../../..")

  # AD19: bodies from ops/aws/ssm/<name>.sh.
  ssm_documents = {
    for name in ["jarvis-deploy", "jarvis-restart", "jarvis-secrets-sync", "jarvis-status"] :
    name => file("${local.repo_root}/ops/aws/ssm/${name}.sh")
  }
}

module "network" {
  source = "../../modules/network"

  account_id  = local.account_id
  aws_region  = var.aws_region
  kms_key_arn = module.compute.kms_key_arn
}

module "secrets" {
  source = "../../modules/secrets"

  kms_key_arn = module.compute.kms_key_arn
}

module "artifacts" {
  source = "../../modules/artifacts"

  account_id            = local.account_id
  bucket_name           = local.artifacts_bucket
  sso_role_arn_prefix   = local.sso_role_arn_prefix
  caller_arn            = data.aws_caller_identity.current.arn
  kms_key_arn           = module.compute.kms_key_arn
  instance_role_arn     = module.compute.instance_role_arn
  bootstrap_source_dir  = "${local.repo_root}/ops/aws"
  bootstrap_output_path = "${path.module}/.build/bootstrap.tar.gz"
}

module "backup" {
  source = "../../modules/backup"

  account_id             = local.account_id
  aws_region             = var.aws_region
  kms_key_arn            = module.compute.kms_key_arn
  backup_daily_schedule  = var.backup_daily_schedule
  backup_weekly_schedule = var.backup_weekly_schedule
}

module "observability" {
  source = "../../modules/observability"

  account_id       = local.account_id
  aws_region       = var.aws_region
  kms_key_arn      = module.compute.kms_key_arn
  alert_email      = var.alert_email
  instance_enabled = var.instance_enabled
  instance_id      = module.compute.instance_id
}

module "compute" {
  source = "../../modules/compute"

  account_id           = local.account_id
  aws_region           = var.aws_region
  instance_type        = var.instance_type
  instance_enabled     = var.instance_enabled
  subnet_id            = module.network.subnet_id
  security_group_id    = module.network.security_group_id
  backup_role_arn      = module.backup.role_arn
  value_secret_arns    = module.secrets.value_secret_arns
  token_secret_arns    = module.secrets.token_secret_arns
  tailscale_secret_id  = module.secrets.tailscale_secret_id
  artifacts_bucket     = local.artifacts_bucket
  bootstrap_key        = module.artifacts.bootstrap_key
  bootstrap_sha256     = module.artifacts.bootstrap_sha256
  auto_reboot_time_utc = var.auto_reboot_time_utc
  log_group_names      = module.observability.log_group_names
  alert_topic_arn      = module.observability.alert_topic_arn
}

module "ssm" {
  source = "../../modules/ssm"

  documents = local.ssm_documents
}

module "tailscale_acl" {
  source = "../../modules/tailscale_acl"

  manage_tailscale_acl = var.manage_tailscale_acl
}
