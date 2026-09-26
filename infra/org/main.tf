data "aws_organizations_organization" "this" {}

locals {
  account_id = aws_organizations_account.jarvis_prod.id
  ou_id      = aws_organizations_organizational_unit.jarvis.id
  parent_id  = coalesce(var.parent_id, data.aws_organizations_organization.this.roots[0].id)

  scp_type_enabled = anytrue(flatten([
    for r in data.aws_organizations_organization.this.roots : [
      for p in r.policy_types : p.type == "SERVICE_CONTROL_POLICY" && p.status == "ENABLED"
    ]
  ]))

  scps = {
    "jarvis-region-deny" = { description = "Deny regional APIs outside the home region (DESIGN.md 11.1)", attach = var.attach_region_scp }
    "jarvis-org-guard"   = { description = "Deny leaving the org and CloudTrail tampering (DESIGN.md 11.2)", attach = true }
    "jarvis-guardrail"   = { description = "Deny IAM users, access keys, key pairs, SG ingress (DESIGN.md 11.3)", attach = var.attach_guardrail_scp }
  }
}

# AD43: a deletable OU holds the account; `terraform destroy` here closes the account (90-day
# AWS recovery window) and then deletes the empty OU.
resource "aws_organizations_organizational_unit" "jarvis" {
  name      = "jarvis"
  parent_id = local.parent_id
}

resource "aws_organizations_account" "jarvis_prod" {
  name              = var.account_name
  email             = var.account_email
  parent_id         = local.ou_id
  role_name         = "OrganizationAccountAccessRole"
  close_on_deletion = true

  # ForceNew fields are ignored so a drift never plans a replace; `terraform destroy` is the only close path.
  lifecycle {
    ignore_changes = [role_name, email, iam_user_access_to_billing]
  }
}

resource "aws_organizations_policy" "scp" {
  for_each = local.scps

  name        = each.key
  description = each.value.description
  type        = "SERVICE_CONTROL_POLICY"
  content     = local.policies["scp/${each.key}"]
}

resource "aws_organizations_policy_attachment" "scp" {
  for_each = { for k, v in local.scps : k => v if v.attach }

  policy_id = aws_organizations_policy.scp[each.key].id
  target_id = local.ou_id

  lifecycle {
    precondition {
      condition     = local.scp_type_enabled
      error_message = "The SERVICE_CONTROL_POLICY policy type is not enabled on the organization root; enable it before attaching SCPs."
    }
  }
}
