output "bucket" {
  description = "Artifacts bucket name."
  value       = aws_s3_bucket.artifacts.bucket
}

output "bucket_arn" {
  description = "Artifacts bucket ARN."
  value       = aws_s3_bucket.artifacts.arn
}

output "bootstrap_key" {
  description = "S3 key of the bootstrap tarball (bootstrap/<sha256>/bootstrap.tar.gz)."
  value       = aws_s3_object.bootstrap.key
}

output "bootstrap_sha256" {
  description = "sha256 of the bootstrap tarball."
  value       = data.archive_file.bootstrap.output_sha256
}

output "policies" {
  description = "Every policy document this module creates, keyed by policy name (read by infra/tests)."
  value       = local.policies
}
