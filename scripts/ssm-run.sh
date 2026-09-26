#!/usr/bin/env bash
# scripts/ssm-run.sh <document-name> <instance-id> [aws ssm send-command args...]: sends an SSM
# command, waits for it to reach a terminal state, prints its stdout (and stderr on failure), and
# exits non-zero if the command itself failed. Shared by the Makefile's deploy/status/restart/
# secrets-sync targets (M1) so each doesn't repeat the send+wait+fetch+print dance.
set -euo pipefail

DOCUMENT="${1:?usage: ssm-run.sh <document-name> <instance-id> [aws ssm send-command args...]}"
INSTANCE_ID="${2:?usage: ssm-run.sh <document-name> <instance-id> [aws ssm send-command args...]}"
shift 2
AWS_REGION="${AWS_REGION:-us-east-2}"

AWS_PROFILE_OPT=()
[ -n "${AWS_PROFILE:-}" ] && AWS_PROFILE_OPT=(--profile "$AWS_PROFILE")

log() { printf '[ssm-run] %s\n' "$*" >&2; }

cmd_id="$(aws ssm send-command --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" \
  --instance-ids "$INSTANCE_ID" --document-name "$DOCUMENT" "$@" \
  --query "Command.CommandId" --output text)"
log "command $cmd_id sent ($DOCUMENT on $INSTANCE_ID)"

status="Success"
if ! aws ssm wait command-executed --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" \
     --command-id "$cmd_id" --instance-id "$INSTANCE_ID"; then
  status="Failed"
fi

out="$(aws ssm get-command-invocation --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" \
  --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
  --query StandardOutputContent --output text 2>/dev/null || true)"
[ -n "$out" ] && printf '%s\n' "$out"

if [ "$status" != "Success" ]; then
  err="$(aws ssm get-command-invocation --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
    --query StandardErrorContent --output text 2>/dev/null || true)"
  [ -n "$err" ] && printf '%s\n' "$err" >&2
  log "command failed ($DOCUMENT, command-id $cmd_id)"
  exit 1
fi
