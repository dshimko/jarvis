variable "account_id" {
  description = "jarvis-prod account id."
  type        = string
}

variable "aws_region" {
  description = "Home region."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the jarvis CMK."
  type        = string
}

variable "alert_email" {
  description = "Email subscribed to jarvis-alerts (AD22). The human confirms the subscription."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "instance_enabled" {
  description = "Whether the instance exists (instance-scoped alarms are created only then)."
  type        = bool
}

variable "instance_id" {
  description = "Instance id for the disk and status alarms (null while instance_enabled = false)."
  type        = string
  default     = null
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 30
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD."
  type        = number
  default     = 60
}

variable "budget_thresholds_percent" {
  description = "ACTUAL cost alert thresholds."
  type        = list(number)
  default     = [50, 80, 100]
}
