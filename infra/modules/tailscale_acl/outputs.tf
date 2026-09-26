output "managed" {
  description = "Whether Terraform manages the tailnet policy."
  value       = var.manage_tailscale_acl
}

output "policies" {
  description = "Policy documents this module creates (none), keyed by policy name (read by infra/tests)."
  value       = local.policies
}
