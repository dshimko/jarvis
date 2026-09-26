output "account_id" {
  description = "jarvis-prod account id."
  value       = local.account_id
}

output "instance_id" {
  description = "Instance id (null while instance_enabled = false)."
  value       = module.compute.instance_id
}

output "eip_public_ip" {
  description = "Elastic IP of the instance (the AD29 source-IP pin)."
  value       = module.compute.eip_public_ip
}

output "kms_key_arn" {
  description = "ARN of alias/jarvis."
  value       = module.compute.kms_key_arn
}

output "artifacts_bucket" {
  description = "Artifacts bucket for make release."
  value       = module.artifacts.bucket
}

output "bootstrap_key" {
  description = "S3 key of the current bootstrap tarball."
  value       = module.artifacts.bootstrap_key
}

output "secret_names" {
  description = "The six secrets (the human populates jarvis/work, personal, shared, tailscale)."
  value       = module.secrets.secret_names
}

output "alert_topic_arn" {
  description = "jarvis-alerts topic."
  value       = module.observability.alert_topic_arn
}

output "ssm_documents" {
  description = "Command documents."
  value       = module.ssm.document_names
}

# Read by infra/tests: every IAM, key, bucket, and topic policy document in this root.
output "iam_policies" {
  description = "Every policy document in envs/prod, keyed by policy name."
  value = merge(
    module.network.policies,
    module.compute.policies,
    module.artifacts.policies,
    module.backup.policies,
    module.observability.policies,
    module.secrets.policies,
    module.ssm.policies,
    module.tailscale_acl.policies,
  )
}

output "iam_policy_count" {
  description = "Sum of per-module policy counts (detects key collisions in iam_policies)."
  value = sum([for p in [
    module.network.policies, module.compute.policies, module.artifacts.policies,
    module.backup.policies, module.observability.policies, module.secrets.policies,
    module.ssm.policies, module.tailscale_acl.policies,
  ] : length(p)])
}

output "managed_policy_attachments" {
  description = "Every AWS managed policy attachment in envs/prod."
  value       = concat(module.compute.managed_policy_attachments, module.backup.managed_policy_attachments)
}

output "security_groups" {
  description = "Security group inventory (no_ingress test)."
  value       = module.network.security_groups
}

output "instance_settings" {
  description = "Launch template settings (no_ingress test: no key pair)."
  value       = module.compute.instance_settings
}
