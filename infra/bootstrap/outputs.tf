output "state_bucket" {
  description = "Bucket for envs/prod backend.hcl."
  value       = aws_s3_bucket.tfstate.bucket
}

output "state_kms_key_arn" {
  description = "ARN of alias/jarvis-tfstate (only if backend.hcl names the key; send the full ARN)."
  value       = aws_kms_key.tfstate.arn
}

output "policies" {
  description = "Every policy document in bootstrap, keyed by policy name (read by infra/tests)."
  value       = merge(local.key_policies, local.policies)
}
