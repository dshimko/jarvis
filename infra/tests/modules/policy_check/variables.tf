variable "policies" {
  description = "Policy documents keyed by policy name (JSON strings)."
  type        = map(string)
}

variable "region" {
  description = "Region the expected ARNs, ViaService hosts, and region conditions use; matches the aws_region/home_region defaults (AD34)."
  type        = string
  default     = "us-east-1"
}
