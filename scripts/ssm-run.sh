#!/usr/bin/env bash
# scripts/ssm-run.sh <document-name> <instance-id> [<timeout-seconds>] [aws ssm send-command args...]
# Sends an SSM command and polls get-command-invocation every POLL_INTERVAL_S (default 10s) until
# a terminal status or the timeout elapses, printing status changes as they happen and the final
# stdout (always) / stderr (on failure). Exits 0 only on Success. Shared by the Makefile's
# deploy/status/restart/secrets-sync/ofw-login/ofw-reset targets (M1).
#
# H2: `aws ssm wait command-executed` is capped internally (20 attempts x 5s = 100s, no CLI
# override) and exits 255 while the command is still legitimately InProgress -- a fresh
# jarvis-deploy (venv build, hash-pinned pip install, the ofw venv and Chromium download, the full
# pytest suite, restarts, /health checks) routinely takes well over 100s, and jarvis-ofw-login
# waits while the human logs in through the port forward. The timeout comes from the caller: the
# third argument when it is a whole number (the Makefile: 1800s for deploy, 300s for
# status/restart/secrets-sync, WaitSeconds+120 for ofw-login), otherwise SSM_RUN_TIMEOUT_S
# (default 300s). SSM_RUN_POLL_INTERVAL_S sets the poll interval.
set -euo pipefail

USAGE="usage: ssm-run.sh <document-name> <instance-id> [<timeout-seconds>] [aws ssm send-command args...]"
DOCUMENT="${1:?$USAGE}"
INSTANCE_ID="${2:?$USAGE}"
shift 2
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
  TIMEOUT_S="$1"
  shift
else
  TIMEOUT_S="${SSM_RUN_TIMEOUT_S:-300}"
fi
REGION="${AWS_REGION:-us-east-1}"
POLL_INTERVAL_S="${SSM_RUN_POLL_INTERVAL_S:-10}"

# Always has at least --region in it, so it is never an empty array: expanding "${AWS_ARGS[@]}"
# under `set -u` on bash < 4.4 (e.g. macOS's shipped /bin/bash 3.2) throws "unbound variable" for
# a declared-but-EMPTY array, a bug fixed only in bash 4.4+; folding --region into this array
# (rather than keeping a separate, possibly-empty profile array) sidesteps it entirely.
AWS_ARGS=(--region "$REGION")
[ -n "${AWS_PROFILE:-}" ] && AWS_ARGS+=(--profile "$AWS_PROFILE")

log() { printf '[ssm-run] %s\n' "$*" >&2; }

cmd_id="$(aws ssm send-command "${AWS_ARGS[@]}" \
  --instance-ids "$INSTANCE_ID" --document-name "$DOCUMENT" "$@" \
  --query "Command.CommandId" --output text)"
log "command $cmd_id sent ($DOCUMENT on $INSTANCE_ID); polling every ${POLL_INTERVAL_S}s, timeout ${TIMEOUT_S}s"

get_status() {
  aws ssm get-command-invocation "${AWS_ARGS[@]}" \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || true
}

print_output() {
  local out err
  out="$(aws ssm get-command-invocation "${AWS_ARGS[@]}" \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
    --query StandardOutputContent --output text 2>/dev/null || true)"
  [ -n "$out" ] && printf '%s\n' "$out"
  err="$(aws ssm get-command-invocation "${AWS_ARGS[@]}" \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
    --query StandardErrorContent --output text 2>/dev/null || true)"
  [ -n "$err" ] && printf '%s\n' "$err" >&2
  return 0  # `[ -n "$err" ] && ...` above is false (and this function's last-executed status)
            # whenever stderr is empty -- the normal case -- which under `set -e` would otherwise
            # make print_output itself "fail" as a simple command and abort the caller (the `case`
            # branches below all run it as a bare statement followed by `exit 0`/more commands).
}

deadline=$(($(date +%s) + TIMEOUT_S))
status="" last_status=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  status="$(get_status)"
  [ -n "$status" ] || status="Pending"
  if [ "$status" != "$last_status" ]; then
    log "status: $status"
    last_status="$status"
  fi
  case "$status" in
    Success | Failed | Cancelled | TimedOut | DeliveryTimedOut) break ;;
  esac
  sleep "$POLL_INTERVAL_S"
done

case "$status" in
  Success)
    print_output
    exit 0
    ;;
  Failed | Cancelled | TimedOut | DeliveryTimedOut)
    print_output
    log "command failed (status $status, $DOCUMENT, command-id $cmd_id)"
    exit 1
    ;;
  *)
    print_output
    log "poll timed out after ${TIMEOUT_S}s (last status: ${status:-unknown}, $DOCUMENT, command-id $cmd_id)"
    exit 1
    ;;
esac
