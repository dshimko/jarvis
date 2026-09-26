variable "account_id" {
  description = "jarvis-prod account id (ACCT in DESIGN.md)."
  type        = string
}

variable "aws_region" {
  description = "Home region."
  type        = string
}

variable "availability_zone" {
  description = "AZ for the single public subnet. Defaults to <region>a."
  type        = string
  default     = null
}

variable "vpc_cidr" {
  description = "VPC CIDR (DESIGN.md section 2). Must not overlap 100.64.0.0/10."
  type        = string
  default     = "10.60.0.0/24"
}

variable "subnet_cidr" {
  description = "Public subnet CIDR."
  type        = string
  default     = "10.60.0.0/26"
}

variable "kms_key_arn" {
  description = "ARN of the jarvis CMK (alias/jarvis), used for the flow log bucket."
  type        = string
}

variable "flow_log_expiration_days" {
  description = "Days before flow log objects expire."
  type        = number
  default     = 90
}
