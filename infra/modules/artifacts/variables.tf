variable "account_id" {
  description = "jarvis-prod account id."
  type        = string
}

variable "bucket_name" {
  description = "Artifacts bucket name (jarvis-artifacts-<ACCT>)."
  type        = string
}

variable "sso_role_arn_prefix" {
  description = "arn:aws:iam::<ACCT>:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_ (no region segment: Identity Center is in us-east-1)."
  type        = string
}

variable "caller_arn" {
  description = "ARN of the principal running Terraform (checked before the bucket policy is created)."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the jarvis CMK."
  type        = string
}

variable "instance_role_arn" {
  description = "ARN of jarvis-instance (read-only principal)."
  type        = string
}

variable "bootstrap_source_dir" {
  description = "Directory packed into the bootstrap tarball (ops/aws, AD18)."
  type        = string
}

variable "bootstrap_output_path" {
  description = "Local path where the bootstrap tarball is built."
  type        = string
}

variable "noncurrent_version_expiration_days" {
  description = "Days before noncurrent object versions expire."
  type        = number
  default     = 30
}
