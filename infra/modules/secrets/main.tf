locals {
  # Seven secrets (AD6, AD7, AD34). Values are set by the human or the instance, never by Terraform.
  value_secrets = {
    work      = { name = "jarvis/work", description = "Work mode env (AD7), set by the human" }
    personal  = { name = "jarvis/personal", description = "Personal mode env (AD7), set by the human" }
    shared    = { name = "jarvis/shared", description = "Keys allowed in both modes (AD7), set by the human" }
    tailscale = { name = "jarvis/tailscale", description = "Tailscale auth key, first boot only (AD7)" }
    ofw       = { name = "jarvis/ofw", description = "OFW MCP server credentials and token hashes (AD34), set by the human" }
  }

  token_secrets = {
    work     = { name = "jarvis/work/api-token", description = "Work API token, written by the instance (AD6)" }
    personal = { name = "jarvis/personal/api-token", description = "Personal API token, written by the instance (AD6)" }
  }
}

# Created empty: no aws_secretsmanager_secret_version, no rotation, no resource policy.
resource "aws_secretsmanager_secret" "value" {
  for_each = local.value_secrets

  name                    = each.value.name
  description             = each.value.description
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_in_days
}

resource "aws_secretsmanager_secret" "token" {
  for_each = local.token_secrets

  name                    = each.value.name
  description             = each.value.description
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_in_days
}
