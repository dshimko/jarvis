#!/usr/bin/env bash
# scripts/ssm-run.sh <document-name> <instance-id> <timeout-seconds> [aws ssm send-command args...]
# Sends an SSM command and polls get-command-invocation every POLL_INTERVAL_S (default 10s) until
# a terminal status or <timeout-seconds> elapses, printing status changes as they happen and the
# final stdout (always) / stderr (on failure). Exits 0 only on Success.
#
# H2: `aws ssm wait command-executed` is capped internally (20 attempts x 5s = 100s, no CLI
# override) and exits 255 while the command is still legitimately InProgress -- a fresh
# jarvis-deploy (venv build, hash-pinned pip install, the full pytest suite, two restarts, two
# /health checks) routinely takes well over 100s. <timeout-seconds> is passed in by the caller
# (the Makefile: 1800s for deploy, 300s for status/restart/secrets-sync) instead of trusting that
# waiter's own fixed cap.
set -euo pipefail

DOCUMENT="${1:?usage: ssm-run.sh <document-name> <instance-id> <timeout-seconds> [aws ssm send-command args...]}"
INSTANCE_ID="${2:?usage: ssm-run.sh <document-name> <instance-id> <timeout-seconds> [aws ssm send-command args...]}"
TIMEOUT_S="${3:?usage: ssm-run.sh <document-name> <instance-id> <timeout-seconds> [aws ssm send-command args...]}"
shift 3
REGION="${AWS_REGION:-us-east-1}"
POLL_INTERVAL_S="${SSM_RUN_POLL_INTERVAL_S:-10}"

AWS_PROFILE_OPT=()
[ -n "${AWS_PROFILE:-}" ] && AWS_PROFILE_OPT=(--profile "$AWS_PROFILE")

log() { printf '[ssm-run] %s\n' "$*" >&2; }

cmd_id="$(aws ssm send-command --region "$REGION" "${AWS_PROFILE_OPT[@]+"${AWS_PROFILE_OPT[@]}"}" \
  --instance-ids "$INSTANCE_ID" --document-name "$DOCUMENT" "$@" \
  --query "Command.CommandId" --output text)"
log "command $cmd_id sent ($DOCUMENT on $INSTANCE_ID); polling every ${POLL_INTERVAL_S}s, timeout ${TIMEOUT_S}s"

get_status() {
  aws ssm get-command-invocation --region "$REGION" "${AWS_PROFILE_OPT[@]+"${AWS_PROFILE_OPT[@]}"}" \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || true
}

print_output() {
  local out err
  out="$(aws ssm get-command-invocation --region "$REGION" "${AWS_PROFILE_OPT[@]+"${AWS_PROFILE_OPT[@]}"}" \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
    --query StandardOutputContent --output text 2>/dev/null || true)"
  [ -n "$out" ] && printf '%s\n' "$out"
  err="$(aws ssm get-command-invocation --region "$REGION" "${AWS_PROFILE_OPT[@]+"${AWS_PROFILE_OPT[@]}"}" \
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
