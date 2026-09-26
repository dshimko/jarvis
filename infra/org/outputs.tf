output "account_id" {
  description = "jarvis-prod account id (ACCT)."
  value       = aws_organizations_account.jarvis_prod.id
}

output "ou_id" {
  description = "Id of the jarvis OU that holds jarvis-prod and carries the SCPs (AD35)."
  value       = aws_organizations_organizational_unit.jarvis.id
}

output "scp_ids" {
  description = "SCP ids by name."
  value       = { for k, p in aws_organizations_policy.scp : k => p.id }
}

output "attached_scps" {
  description = "SCPs attached to the jarvis OU."
  value       = sort(keys(aws_organizations_policy_attachment.scp))
}

output "permission_set_arns" {
  description = "Permission set ARNs (empty unless create_permission_sets)."
  value       = { for k, p in aws_ssoadmin_permission_set.this : k => p.arn }
}

output "policies" {
  description = "Every policy document in org/, keyed by policy name (read by infra/tests)."
  value       = local.policies
}

output "managed_policy_attachments" {
  description = "AWS managed policy attachments for the allowlist test."
  value       = [for a in aws_ssoadmin_managed_policy_attachment.admin : { attached_to = "JarvisAdmin", policy_arn = a.managed_policy_arn }]
}

output "region_deny_policy_json" {
  description = "Rendered S1 for the Access Analyzer dry run (DESIGN.md 11.1)."
  value       = local.policies["scp/jarvis-region-deny"]
}
