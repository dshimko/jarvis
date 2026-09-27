# tailscale_acl (opt-in)

Applies `policy.hujson` as the tailnet policy when `manage_tailscale_acl = true` (default
`false`, AD24). The policy is one document for the whole tailnet, so applying it replaces every
existing rule: merge your tailnet's rules into `policy.hujson` first. Replace the
`REPLACE_ME@example.com` placeholder and confirm the workstation group name (DESIGN.md U2).

One grant, `group:jarvis-workstation -> tag:jarvis`: `tcp:8781` (work API), `tcp:8782`
(personal API), `tcp:8783` (ofw-mcp, read-only, AD33 amendment 2026-09-27 -- OFW Companion on
the workstation as a consumer), `tcp:22000`/`udp:22000` and `tcp:22001`/`udp:22001` (the two
Syncthing folder ports). `tag:jarvis` is never a source: the instance initiates nothing on the
tailnet.

With the flag on, the tailscale provider reads `TAILSCALE_API_KEY` (or OAuth client variables)
from the environment. With it off, `envs/prod` gives the provider an inert placeholder so plans
need no Tailscale credentials; no resource is created and no API call is made.

Whichever way the policy is applied, it must be in place and verified before the instance's first
boot (PLAN 3.2, AD24).
