#!/usr/bin/env bash
# scripts/sync-agents.sh <mode> <instance-id>: `make sync-agents MODE=<mode>` applies template
# changes to CLAUDE.md and .claude/ that a deploy never touches (infra/DESIGN.md section 8.3,
# infra/PLAN.md deploy-engineer deliverable 4). The comparison is between the vault template
# shipped in the currently-deployed release (/opt/jarvis/current/vaults/<mode>/) and the live
# vault (/home/jarvis-<mode>/vault/) -- both already on the instance, so the whole operation runs
# there. Applies with `runuser -u jarvis-<mode> -- rsync` (AD31): rsync itself always runs as the
# mode user, never as root, and only ever touches CLAUDE.md and .claude/ -- never any other file
# in the vault.
#
# Runs entirely inside an AWS-StartInteractiveCommand session (send-command's regular
# get-command-invocation would leave the itemized diff -- which can echo file paths from a
# personal vault -- in SSM's stored command history; an interactive session has logging off by
# design, DESIGN.md section 8.3, and nothing is stored server-side). This also matches
# JarvisOperator's actual permissions, which only cover the four jarvis-* documents plus this
# AWS-managed one, not AWS-RunShellScript/send-command in general.
set -euo pipefail

MODE="${1:-}"
INSTANCE_ID="${2:-}"
REGION="${AWS_REGION:-us-east-1}"

# stderr, not stdout: run_remote() is called as `diff="$(run_remote ...)"`, so anything log()/
# fail() print to stdout would be silently swallowed into $diff (and, on failure, never seen at
# all -- the script exits via errexit at that assignment before main() ever prints $diff).
log() { printf '[sync-agents] %s\n' "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }

case "$MODE" in
  work | personal) ;;
  *) fail "usage: scripts/sync-agents.sh <work|personal> <instance-id>" ;;
esac
[ -n "$INSTANCE_ID" ] || fail "usage: scripts/sync-agents.sh <work|personal> <instance-id>"

require_tools() {
  command -v aws >/dev/null 2>&1 || fail "aws CLI not found"
  command -v session-manager-plugin >/dev/null 2>&1 \
    || fail "session-manager-plugin not found (brew install --cask session-manager-plugin)"
}

# Single quotes around $mode/$src/$dst are intentional: this whole string is embedded, unexpanded,
# into a remote shell command below (its own $mode/$src/$dst expand there, on the instance).
# H2: "$src/.claude" has NO trailing slash -- a trailing slash on an rsync source directory copies
# its CONTENTS into the destination (i.e. straight into vault/, not vault/.claude/). Without the
# slash, the directory itself is copied/merged as vault/.claude/, which is what we want.
# R1: AWS-StartInteractiveCommand runs as ssm-user, not root, so `runuser` alone fails ("may be
# used only by root") -- `sudo runuser` first becomes root, then drops to the mode user. The
# trailing `printf "__rc=%%s\n" "$?"` (the local printf below turns %%s into a literal %s) is an
# explicit exit-status marker: this document reports only the SESSION's own exit status, which is
# 0 regardless of whether the sudo/runuser/rsync command inside it succeeded.
# shellcheck disable=SC2016
REMOTE_RSYNC_TEMPLATE='mode="%s"; src="/opt/jarvis/current/vaults/$mode"; dst="/home/jarvis-$mode/vault"; sudo runuser -u "jarvis-$mode" -- rsync -rlptD %s --itemize-changes "$src/CLAUDE.md" "$src/.claude" "$dst/"; printf "__rc=%%s\n" "$?"'

# Filters the AWS CLI's own session banner lines out of the interactive command's combined
# output, so the human sees only the rsync diff and callers can reliably test "any changes?" on
# the remainder.
strip_session_banner() {
  grep -Ev '^(Starting session with SessionId|Exiting session with sessionId)' || true
}

run_remote() {
  local dry_flag="$1" remote_cmd params_file raw rc output marker
  # shellcheck disable=SC2059  # REMOTE_RSYNC_TEMPLATE is our own fixed format string, not user input
  remote_cmd="$(printf "$REMOTE_RSYNC_TEMPLATE" "$MODE" "$dry_flag")"
  params_file="$(mktemp)"
  python3 -c 'import json, sys; print(json.dumps({"command": [sys.argv[1]]}))' "$remote_cmd" >"$params_file"

  if raw="$(aws ssm start-session --region "$REGION" --target "$INSTANCE_ID" \
       --document-name AWS-StartInteractiveCommand --parameters "file://$params_file" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  rm -f -- "$params_file"

  output="$(printf '%s\n' "$raw" | strip_session_banner)"
  # `|| true` on both: grep exits 1 when nothing matches (e.g. the marker is genuinely absent, or
  # the diff body is genuinely empty once the marker line is excluded), and under `set -e` that
  # would otherwise abort the whole script right here -- silently, before the explicit fail()
  # check below ever runs. Softening it to an empty value lets that check do its job instead.
  marker="$(printf '%s\n' "$output" | grep -o '__rc=[0-9]*' | tail -n 1)" || true
  if [ "$rc" -ne 0 ] || [ "$marker" != "__rc=0" ]; then
    fail "remote rsync failed (session exit $rc, marker '${marker:-none}'): $output"
  fi
  printf '%s\n' "$output" | grep -v '^__rc=' || true
}

main() {
  require_tools
  log "computing diff for $MODE (release template vs. live vault) ..."
  local diff
  diff="$(run_remote "--dry-run")"
  if [ -z "$(printf '%s' "$diff" | tr -d '[:space:]')" ]; then
    log "no changes: CLAUDE.md and .claude/ already match the deployed release for $MODE"
    exit 0
  fi
  printf '%s\n' "$diff"
  printf 'Apply these changes to the live %s vault? [y/N] ' "$MODE"
  read -r answer
  case "$answer" in
    y | Y | yes | YES) ;;
    *) log "not applying"; exit 0 ;;
  esac
  run_remote "" >/dev/null
  log "applied"
}

main
