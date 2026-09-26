variable "manage_tailscale_acl" {
  description = "Apply policy.hujson as the whole tailnet policy (AD24). Default false."
  type        = bool
  default     = false
}

variable "policy_file" {
  description = "Path to the tailnet policy file."
  type        = string
  default     = null
}
