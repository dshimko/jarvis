variable "documents" {
  description = "Command document bodies keyed by name, from ops/aws/ssm/<name>.sh (AD19)."
  type        = map(string)

  validation {
    condition     = toset(keys(var.documents)) == toset(["jarvis-deploy", "jarvis-restart", "jarvis-secrets-sync", "jarvis-status"])
    error_message = "documents must have exactly jarvis-deploy, jarvis-restart, jarvis-secrets-sync, jarvis-status."
  }
}

variable "idle_session_timeout_minutes" {
  description = "Session Manager idle timeout."
  type        = number
  default     = 20
}
