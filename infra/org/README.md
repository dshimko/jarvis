# org

Runs in the management account (`AWS_PROFILE=sparko` by default in `make plan-org`; AD21).
The management identity is used only to plan, apply, and later destroy this root (AD36).

**State is local (AD36):** `infra/org/terraform.tfstate` (and `.backup`), gitignored. It is the
only record of the OU and the account resources, so keep it safe: copy it somewhere private and
backed up after every apply. If it is lost, the OU and account must be imported by hand before
this root can manage or destroy them. There is no `backend.hcl` here.

Creates an OU named `jarvis` under the organization root (or `parent_id` if set), the
`jarvis-prod` account inside it (`close_on_deletion = true`, AD35), and three SCPs attached to
the OU (so they apply to the account through it):

| SCP | Attached to the OU |
|---|---|
| `jarvis-org-guard` (S2) | always |
| `jarvis-region-deny` (S1) | only with `attach_region_scp = true` (default `false`) |
| `jarvis-guardrail` (S3) | only with `attach_guardrail_scp = true` (default `false`, AD27) |

Every attachment has a precondition that the `SERVICE_CONTROL_POLICY` type is enabled on the
organization root, so the plan fails instead of the apply. Outputs include `ou_id` and
`account_id`.

**Teardown (AD35):** run `terraform destroy` here only after `envs/prod` and `bootstrap` have been
destroyed. One `destroy` detaches every SCP from the OU, closes the `jarvis-prod` account, and
deletes the OU -- SCP detachment is not a separate step, it happens as part of this destroy. AWS
keeps a closed account recoverable for 90 days (`SUSPENDED` state); closure becomes permanent, and
irreversible, only after that window, and the account's root email cannot be reused for a new
account until then. While `SUSPENDED` the account is still attached to the `jarvis` OU, so this
first `destroy` closes the account but then fails to delete the now-non-empty OU. Run
`terraform destroy` a second time once the account has left the OU (on its own, or moved out by
hand) to delete the OU and finish.

**Region-deny SCPs have broken real services.** An SCP evaluates the region of the request, so
services that call us-east-1 internally (ACM for CloudFront, Route 53 DNSSEC keys, the STS
global endpoint, billing/budgets/support/health consoles, S3 console calls, Identity Center
admin) fail with an `AccessDenied` that looks like an IAM bug. Before `attach_region_scp = true`
(DESIGN.md 11.1, AD28):

1. `make plan-org` with the flag `false`; take `region_deny_policy_json` from the plan and diff
   its `NotAction` list against the current AWS Control Tower region-deny control.
2. `aws accessanalyzer validate-policy --policy-type SERVICE_CONTROL_POLICY --policy-document
   file://region-deny.json --region us-east-1` must report no `ERROR` or `SECURITY_WARNING`.
3. Attach in a planned window, then smoke test: `make plan`, `make status`, a client token fetch.
   Rollback is setting the flag back to `false` and applying from the management account.

With `create_permission_sets = true` (default `false`, AD25) it also creates `JarvisClient`
(PT1H), `JarvisOperator` (PT4H), and `JarvisAdmin` (PT1H, AdministratorAccess) in the existing
Identity Center instance (us-east-1), and assigns the human's user (`operator_principal_id`) to
Operator and Admin and a dedicated workstation user (`workstation_principal_id`) to Client only.
