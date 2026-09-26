#!/usr/bin/env bash
# jarvis-ofw-reset: the SSM `jarvis-ofw-reset` document body (PLAN.md AD40; no parameters).
# Closes the ofw-mcp login breaker by removing breaker.json as jarvis-ofw (AD31: root never
# touches a path under /home/jarvis-ofw directly), then, if the unit is active, prints the
# breaker field of /healthz so the human can confirm the reset took. Never touches the unit
# itself: ofw-mcp reads breaker.json on its next login attempt, no restart required.
set -euo pipefail

STATE_DIR="${JARVIS_OFW_STATE_DIR:-/home/jarvis-ofw/.local/state/ofw-mcp}"

log() { printf '[jarvis-ofw-reset] %s\n' "$*"; }

log "removing the ofw-mcp login breaker"
runuser -u jarvis-ofw -- rm -f -- "$STATE_DIR/breaker.json"

if ! systemctl is-active --quiet ofw-mcp.service; then
  log "breaker: unknown (ofw-mcp.service is not active)"
  exit 0
fi

# Loopback, unauthenticated (AD33): a plain GET, same as jarvis-status. Prints only the enum
# value ("open", "closed", or "unknown" when the field is missing/malformed) -- never the raw
# response body.
healthz="$(curl -s -m 2 "http://127.0.0.1:8783/healthz" 2>/dev/null || true)"
breaker="unknown"
if [ -n "$healthz" ]; then
  breaker="$(printf '%s' "$healthz" \
    | jq -r 'if (.breaker=="open" or .breaker=="closed") then .breaker else "unknown" end' 2>/dev/null || true)"
  [ -n "$breaker" ] || breaker="unknown"
fi
log "breaker: $breaker"
