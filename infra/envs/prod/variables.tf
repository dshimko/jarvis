variable "expected_account_id" {
  description = "The jarvis-prod account id; the provider refuses any other account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must be a 12-digit account id."
  }
}

variable "aws_region" {
  description = "Home region (AD20)."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "t4g.medium (arm64); set t3.medium to fall back to x86_64 (AD20)."
  type        = string
  default     = "t4g.medium"

  validation {
    condition     = can(regex("^(t4g|t3)\\.", var.instance_type))
    error_message = "instance_type must be t4g.* (arm64) or t3.* (x86_64)."
  }
}

variable "instance_enabled" {
  description = "false for the first apply, true after the four value secrets are populated (AD30)."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Email for jarvis-alerts (AD22). Required, no default."
  type        = string
}

variable "manage_tailscale_acl" {
  description = "Manage the whole tailnet policy from modules/tailscale_acl/policy.hujson (AD24)."
  type        = bool
  default     = false
}

variable "backup_daily_schedule" {
  description = "Daily backup schedule (AD30)."
  type        = string
  default     = "cron(0 8 * * ? *)"
}

variable "backup_weekly_schedule" {
  description = "Weekly backup schedule (AD30)."
  type        = string
  default     = "cron(0 9 ? * SUN *)"
}

variable "auto_reboot_time_utc" {
  description = "unattended-upgrades reboot time, HH:MM UTC, after the backup windows (AD30)."
  type        = string
  default     = "09:30"
}
