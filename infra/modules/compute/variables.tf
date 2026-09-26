variable "account_id" {
  description = "jarvis-prod account id."
  type        = string
}

variable "aws_region" {
  description = "Home region."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. t4g.* selects the arm64 AMI, anything else x86_64 (AD20)."
  type        = string
  default     = "t4g.medium"

  validation {
    condition     = can(regex("^(t4g|t3)\\.", var.instance_type))
    error_message = "instance_type must be t4g.* (arm64) or t3.* (x86_64); the AMI architecture is derived from it."
  }
}

variable "instance_enabled" {
  description = "false for the first apply (populate secrets), then true (AD30)."
  type        = bool
  default     = true
}

variable "subnet_id" {
  description = "Subnet for the primary ENI."
  type        = string
}

variable "security_group_id" {
  description = "Instance security group (zero ingress)."
  type        = string
}

variable "root_volume_size_gib" {
  description = "Root gp3 volume size."
  type        = number
  default     = 30
}

variable "backup_role_arn" {
  description = "ARN of the jarvis-backup role (key policy BackupRoleUse/BackupRoleGrants)."
  type        = string
}

variable "value_secret_arns" {
  description = "ARNs of the four value secrets: work, personal, shared, tailscale."
  type        = map(string)

  validation {
    condition     = toset(keys(var.value_secret_arns)) == toset(["work", "personal", "shared", "tailscale"])
    error_message = "value_secret_arns must have exactly the keys work, personal, shared, tailscale."
  }
}

variable "token_secret_arns" {
  description = "ARNs of the two API token secrets: work, personal."
  type        = map(string)

  validation {
    condition     = toset(keys(var.token_secret_arns)) == toset(["work", "personal"])
    error_message = "token_secret_arns must have exactly the keys work, personal."
  }
}

variable "tailscale_secret_id" {
  description = "Secret name holding the Tailscale auth key (read by cloud-init)."
  type        = string
  default     = "jarvis/tailscale"
}

variable "artifacts_bucket" {
  description = "Artifacts bucket name."
  type        = string
}

variable "bootstrap_key" {
  description = "S3 key of the bootstrap tarball: bootstrap/<sha256>/bootstrap.tar.gz."
  type        = string
}

variable "bootstrap_sha256" {
  description = "sha256 of the bootstrap tarball (checked by user_data before extracting)."
  type        = string
}

variable "auto_reboot_time_utc" {
  description = "unattended-upgrades Automatic-Reboot-Time, HH:MM UTC (AD30)."
  type        = string
  default     = "09:30"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]$", var.auto_reboot_time_utc))
    error_message = "auto_reboot_time_utc must be HH:MM (24h, UTC)."
  }
}

variable "log_group_names" {
  description = "The pre-created log groups the agent writes to (from the observability module)."
  type        = list(string)
}

variable "alert_topic_arn" {
  description = "SNS topic for the auto-recover alarm."
  type        = string
}
