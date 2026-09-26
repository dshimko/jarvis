output "files_scanned" {
  description = "Files scanned."
  value       = local.files
}

output "violations" {
  description = "Source-level violations; empty means clean."
  value = concat(local.resource_type_hits, local.data_type_hits, local.key_name_hits, local.ingress_hits,
  local.inline_blk, local.policy_attr_hits, local.modules_missing_output, local.bad_policies_output, local.root_policy_hits)
}

output "managed_attachment_resources" {
  description = "Every managed-policy attachment resource address, by file."
  value       = local.managed_attachment_resources
}

output "monitoring_resources" {
  description = "Every secret, log group, metric filter, and alarm resource address, by file (ofw inventory)."
  value       = local.monitoring_resources
}

output "security_group_resources" {
  description = "Every security group resource address, by file (no_ingress inventory)."
  value       = local.security_group_resources
}
