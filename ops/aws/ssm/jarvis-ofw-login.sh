#!/usr/bin/env bash
# jarvis-ofw-login: the SSM `jarvis-ofw-login` document body (PLAN.md AD40; WaitSeconds parameter
# -> WAIT_SECONDS env var, allowedPattern ^[0-9]{1,4}$, default 900). Stops ofw-mcp.service, runs
# a headed-from-the-browser-side login as jarvis-ofw (the bundled Chromium listens on
# 127.0.0.1:9222; the human drives it from chrome://inspect through the `make ofw-login` SSM port
# forward), and restarts the unit again no matter what happened. Prints only a fixed status word:
# never anything the browser or the `ofw-mcp login` CLI itself emits (no OFW content, AD33/AD36
# rules 0.4).
set -euo pipefail

JARVIS_ROOT="${JARVIS_ROOT:-/opt/jarvis}"
OFW_MCP_BIN="${JARVIS_OFW_MCP_BIN:-$JARVIS_ROOT/current/ofw-venv/bin/ofw-mcp}"
WAIT_SECONDS="${WAIT_SECONDS:-${1:-900}}"
WAIT_RE='^[0-9]{1,4}$'  # matches the SSM document's WaitSeconds allowedPattern exactly (unchanged)

log() { printf '[jarvis-ofw-login] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

[[ "$WAIT_SECONDS" =~ $WAIT_RE ]] || fail "WaitSeconds must be 1 to 4 digits, got '$WAIT_SECONDS'"
# Gate fix (item 4): the Terraform allowedPattern only bounds the shape (1-4 digits), not the
# range; ofw-mcp's own CLI bound is MAX_LOGIN_WAIT_SECONDS=3600 (cli.py), and outside 1..3600 it
# exits 2 -- the SAME code as a genuine timeout. Re-validated here so a bad value is reported as
# bad, not misreported as "timeout".
[ "$WAIT_SECONDS" -ge 1 ] && [ "$WAIT_SECONDS" -le 3600 ] \
  || fail "WaitSeconds must be 1 to 3600, got '$WAIT_SECONDS'"
[ -x "$OFW_MCP_BIN" ] || fail "$OFW_MCP_BIN not found; deploy a release with ofw-mcp/ first"

# A trap, not a plain call at the bottom: always starts the unit again, on success, failure, or
# timeout (including a failed `systemctl stop` below), never leaving it down.
# shellcheck disable=SC2329  # invoked indirectly via `trap restart_unit EXIT`
restart_unit() {
  local ec=$?
  log "restarting ofw-mcp.service"
  systemctl start ofw-mcp.service || log "FAIL: ofw-mcp.service did not restart; check it by hand"
  return "$ec"
}
trap restart_unit EXIT

log "stopping ofw-mcp.service for the manual login"
systemctl stop ofw-mcp.service

# AD31: every path under /home/jarvis-ofw is touched as that user, never as root. `ofw-mcp login`
# never fills credentials; it only opens the bundled Chromium on the sign-in page and waits for
# the human to leave it (state.json is written on success). Nothing this command prints on
# stdout/stderr is echoed by this script -- only the fixed words below, keyed off its exit code
# (EXIT_OK=0, EXIT_TIMEOUT=2, defined in ofw-mcp's own CLI).
set +e
runuser -u jarvis-ofw -- env HOME=/home/jarvis-ofw \
  PLAYWRIGHT_BROWSERS_PATH=/home/jarvis-ofw/.cache/ms-playwright \
  OFW_MCP_PROFILE=aws OFW_TZ=America/Detroit \
  "$OFW_MCP_BIN" login --wait "$WAIT_SECONDS" >/dev/null 2>&1
rc=$?
set -e

case "$rc" in
  0) log "state saved" ;;
  2) log "timeout" ;;
  *) log "failed" ;;
esac
exit "$rc"
