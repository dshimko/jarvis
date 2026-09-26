variable "account_id" {
  description = "jarvis-prod account id."
  type        = string
}

variable "aws_region" {
  description = "Home region."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the jarvis CMK for the vault."
  type        = string
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

variable "daily_retention_days" {
  description = "Daily recovery point retention."
  type        = number
  default     = 14
}

variable "weekly_retention_days" {
  description = "Weekly recovery point retention (8 weeks)."
  type        = number
  default     = 56
}
