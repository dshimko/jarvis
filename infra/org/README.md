# org

Runs in the management account (`AWS_PROFILE=sparko` by default in `make plan-org`; AD21).
Its state lives in a management-account S3 bucket named in `backend.hcl` (not created here).

Creates the `jarvis-prod` account (`close_on_deletion = false`, `prevent_destroy`) and three SCPs:

| SCP | Attached |
|---|---|
| `jarvis-org-guard` (S2) | always |
| `jarvis-region-deny` (S1) | only with `attach_region_scp = true` (default `false`) |
| `jarvis-guardrail` (S3) | only with `attach_guardrail_scp = true` (default `false`, AD27) |

Every attachment has a precondition that the `SERVICE_CONTROL_POLICY` type is enabled on the
organization root, so the plan fails instead of the apply.

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
