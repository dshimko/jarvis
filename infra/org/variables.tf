variable "identity_center_region" {
  description = "Region of the Identity Center instance and the provider (confirmed us-east-1)."
  type        = string
  default     = "us-east-1"
}

variable "home_region" {
  description = "The only region the region-deny SCP allows, and the region in permission-set ARNs."
  type        = string
  default     = "us-east-1"
}

variable "account_name" {
  description = "Name of the member account."
  type        = string
  default     = "jarvis-prod"
}

variable "account_email" {
  description = "Root email of the new jarvis-prod account. Required."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.account_email))
    error_message = "account_email must be an email address."
  }
}

variable "parent_id" {
  description = "Parent (root or OU id) of the jarvis OU. null places the OU under the organization root."
  type        = string
  default     = null
}

variable "attach_region_scp" {
  description = "Attach jarvis-region-deny. Only after the dry run in DESIGN.md 11.1 (AD28)."
  type        = bool
  default     = false
}

variable "attach_guardrail_scp" {
  description = "Attach jarvis-guardrail (AD27). Recommended after the first successful apply."
  type        = bool
  default     = false
}

variable "create_permission_sets" {
  description = "Create JarvisClient/Operator/Admin in the existing Identity Center instance (AD25)."
  type        = bool
  default     = false
}

variable "operator_principal_id" {
  description = "Identity Center user id of the human (gets JarvisOperator and JarvisAdmin)."
  type        = string
  default     = null
}

variable "workstation_principal_id" {
  description = "Identity Center user id dedicated to the Windows workstation (gets JarvisClient only)."
  type        = string
  default     = null
}
