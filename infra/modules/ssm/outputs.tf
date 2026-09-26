output "document_names" {
  description = "The four command documents."
  value       = sort([for d in aws_ssm_document.command : d.name])
}

output "session_document_name" {
  description = "Session Manager preferences document."
  value       = aws_ssm_document.session_preferences.name
}

output "policies" {
  description = "Policy documents this module creates (none), keyed by policy name (read by infra/tests)."
  value       = local.policies
}
