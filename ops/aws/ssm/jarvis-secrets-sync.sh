#!/usr/bin/env bash
# jarvis-secrets-sync: the SSM `jarvis-secrets-sync` document body. Runs the root jarvis-secrets
# tool's `sync` subcommand, then restarts only the mode(s) whose env file actually changed and
# prints their names (infra/DESIGN.md section 8.2). jarvis-secrets itself rewrites both env files
# unconditionally on every sync, so the before/after hash comparison that decides "changed" lives
# here rather than in that tool. PLAN.md AD19, AD31 (env files are read via runuser, never a root
# path traversal into a mode user's home).
set -euo pipefail

JARVIS_ROOT="${JARVIS_ROOT:-/opt/jarvis}"
JARVIS_HOME_DIR="${JARVIS_HOME_DIR:-/home}"
JARVIS_SECRETS_BIN="${JARVIS_SECRETS_BIN:-$JARVIS_ROOT/bin/jarvis-secrets}"

log() { printf '[jarvis-secrets-sync] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

env_hash() {
  local mode="$1"
  local path="$JARVIS_HOME_DIR/jarvis-$mode/.jarvis/env"
  runuser -u "jarvis-$mode" -- sha256sum "$path" 2>/dev/null | awk '{print $1}'
}

before_work="$(env_hash work)"
before_personal="$(env_hash personal)"

"$JARVIS_SECRETS_BIN" sync || fail "jarvis-secrets sync failed"

changed=""
[ "$(env_hash work)" = "$before_work" ] || changed="$changed work"
[ "$(env_hash personal)" = "$before_personal" ] || changed="$changed personal"
changed="${changed# }"

if [ -z "$changed" ]; then
  log "no env changes, nothing to restart"
  exit 0
fi

for mode in $changed; do
  log "restarting jarvis@$mode (env changed)"
  systemctl restart "jarvis@$mode"
done
log "changed_modes:$changed"
