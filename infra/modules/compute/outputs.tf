output "kms_key_arn" {
  description = "ARN of the jarvis CMK (alias/jarvis)."
  value       = aws_kms_key.jarvis.arn
}

output "instance_role_arn" {
  description = "ARN of the jarvis-instance role."
  value       = aws_iam_role.instance.arn
}

output "eip_public_ip" {
  description = "Elastic IP (the AD29 source-IP pin)."
  value       = aws_eip.jarvis.public_ip
}

output "instance_id" {
  description = "Instance id, or null while instance_enabled = false."
  value       = one(aws_instance.jarvis[*].id)
}

output "ami_id" {
  description = "AMI selected for the launch template."
  value       = data.aws_ami.ubuntu.id
}

output "policies" {
  description = "Every policy document this module creates, keyed by policy name (read by infra/tests)."
  value       = merge(local.key_policies, local.policies)
}

output "managed_policy_attachments" {
  description = "AWS managed policy attachments (role => policy ARN) for the allowlist test."
  value       = [{ attached_to = aws_iam_role_policy_attachment.ssm_core.role, policy_arn = aws_iam_role_policy_attachment.ssm_core.policy_arn }]
}

output "instance_settings" {
  description = "Launch template settings the tests assert (IMDS, key pair)."
  value = {
    launch_template_key_name = aws_launch_template.jarvis.key_name
    http_tokens              = aws_launch_template.jarvis.metadata_options[0].http_tokens
    hop_limit                = aws_launch_template.jarvis.metadata_options[0].http_put_response_hop_limit
  }
}
