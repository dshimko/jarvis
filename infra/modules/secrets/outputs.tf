output "value_secret_arns" {
  description = "ARNs of jarvis/work, jarvis/personal, jarvis/shared, jarvis/tailscale, jarvis/ofw."
  value       = { for k, s in aws_secretsmanager_secret.value : k => s.arn }
}

output "token_secret_arns" {
  description = "ARNs of jarvis/work/api-token and jarvis/personal/api-token."
  value       = { for k, s in aws_secretsmanager_secret.token : k => s.arn }
}

output "tailscale_secret_id" {
  description = "Name of the Tailscale auth key secret."
  value       = aws_secretsmanager_secret.value["tailscale"].name
}

output "secret_names" {
  description = "All seven secret names."
  value       = concat([for s in aws_secretsmanager_secret.value : s.name], [for s in aws_secretsmanager_secret.token : s.name])
}

output "policies" {
  description = "Policy documents this module creates (none), keyed by policy name (read by infra/tests)."
  value       = local.policies
}
