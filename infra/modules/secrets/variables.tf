variable "kms_key_arn" {
  description = "ARN of the jarvis CMK."
  type        = string
}

variable "recovery_window_in_days" {
  description = "Secrets Manager recovery window."
  type        = number
  default     = 7
}
