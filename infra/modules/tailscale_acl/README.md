# tailscale_acl (opt-in)

Applies `policy.hujson` as the tailnet policy when `manage_tailscale_acl = true` (default
`false`, AD24). The policy is one document for the whole tailnet, so applying it replaces every
existing rule: merge your tailnet's rules into `policy.hujson` first. Replace the
`REPLACE_ME@example.com` placeholder and confirm the workstation group name (DESIGN.md U2).

With the flag on, the tailscale provider reads `TAILSCALE_API_KEY` (or OAuth client variables)
from the environment. With it off, `envs/prod` gives the provider an inert placeholder so plans
need no Tailscale credentials; no resource is created and no API call is made.

Whichever way the policy is applied, it must be in place and verified before the instance's first
boot (PLAN 3.2, AD24).
