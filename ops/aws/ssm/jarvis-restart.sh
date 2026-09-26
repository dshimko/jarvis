#!/usr/bin/env bash
# jarvis-restart: the SSM `jarvis-restart` document body (Mode, Rotate parameters -> MODE, ROTATE
# env vars). Restarts jarvis@<mode>; with Rotate=true, first removes that mode's API token as the
# user (AD31), so the unit's ExecStartPost=+jarvis-secrets publish-token regenerates and
# republishes a fresh one on the next start. infra/DESIGN.md section 8.2; PLAN.md AD19.
set -euo pipefail

JARVIS_HOME_DIR="${JARVIS_HOME_DIR:-/home}"
MODE="${MODE:-${1:-}}"
ROTATE="${ROTATE:-${2:-false}}"

log() { printf '[jarvis-restart] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

case "$MODE" in
  work | personal) ;;
  *) fail "Mode must be 'work' or 'personal', got '${MODE}'" ;;
esac
case "$ROTATE" in
  true | false) ;;
  *) fail "Rotate must be 'true' or 'false', got '${ROTATE}'" ;;
esac

if [ "$ROTATE" = "true" ]; then
  token_path="$JARVIS_HOME_DIR/jarvis-$MODE/.jarvis/api_token"
  log "rotating token for $MODE"
  runuser -u "jarvis-$MODE" -- rm -f -- "$token_path"
fi

log "restarting jarvis@$MODE"
systemctl restart "jarvis@$MODE"
log "restarted jarvis@$MODE rotate=$ROTATE"
