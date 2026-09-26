# secrets

Seven empty Secrets Manager secrets under the jarvis CMK with a 7-day recovery window:
`jarvis/work`, `jarvis/personal`, `jarvis/shared`, `jarvis/tailscale`, `jarvis/ofw` (AD34; values
set by the human with the CLI) and `jarvis/work/api-token`, `jarvis/personal/api-token` (written by the instance's
root `publish-token`, AD6). No secret versions, no rotation Lambda, no resource policies.
