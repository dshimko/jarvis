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

variable "noncurrent_version_expiration_days" {
  description = "Days before noncurrent state versions expire."
  type        = number
  default     = 90
}
