output "role_arn" {
  description = "ARN of the jarvis-backup role."
  value       = aws_iam_role.backup.arn
}

output "vault_name" {
  description = "Backup vault name."
  value       = aws_backup_vault.jarvis.name
}

output "policies" {
  description = "Every policy document this module creates, keyed by policy name (read by infra/tests)."
  value       = local.policies
}

output "managed_policy_attachments" {
  description = "AWS managed policy attachments for the allowlist test."
  value       = [for a in aws_iam_role_policy_attachment.backup : { attached_to = a.role, policy_arn = a.policy_arn }]
}
