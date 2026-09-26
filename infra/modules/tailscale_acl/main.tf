# The tailnet ACL is one document for the whole tailnet: applying it replaces every rule.
# Only the ACL is managed here; no auth keys or devices ever enter state.
resource "tailscale_acl" "this" {
  count = var.manage_tailscale_acl ? 1 : 0

  acl                        = file(coalesce(var.policy_file, "${path.module}/policy.hujson"))
  overwrite_existing_content = true
}
