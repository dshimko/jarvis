#!/usr/bin/env bash
# scripts/oauth-login.sh <mode>: OAuth login for a headless server (infra/BRIEF.md 4.4;
# infra/PLAN.md AD23; infra/DESIGN.md section 8). Forwards the OAuth callback ports from the
# workstation to the instance over SSM, opens an interactive shell as jarvis-<mode> in the vault,
# prints the manual steps, and afterwards verifies the personal/work Gmail/MCP connection by
# asking the vault a question through a second SSM command.
#
# Claude Code MCP OAuth callback port (deploy-engineer report, PLAN.md G10): Context7 docs for
# anthropics/claude-code (queried 2026-09-25) describe the automatic OAuth flow for SSE/HTTP MCP
# servers and a `claude mcp login <name> --no-browser` stdin-redirect path for headless/SSH use,
# but do NOT document a fixed local callback port for that flow (the only fixed port in the docs
# is an unrelated self-hosted gateway's `serve` mode listener, not the client-side `/mcp` OAuth
# callback). Treated as dynamic: OAUTH_PORTS is fully configurable and forwarded verbatim; the
# default below covers only the two ports this repo already documents elsewhere (Slack OAuth
# 3118, the Gmail watcher's own loopback callback 8766 from AD10). If `/mcp` reports a specific
# callback port for a server, either re-run with OAUTH_PORTS=3118,8766,<port>, or prefer
# `claude mcp login <name> --no-browser` inside the SSM shell below, which needs no forwarded
# browser callback port at all.
#
# Every SSM --parameters value below is written as file://<json>, never AWS CLI shorthand: the
# remote commands embed single AND double quotes (sudo/bash -lc/claude -p), which shorthand
# cannot round-trip safely.
set -euo pipefail

MODE="${1:-}"
AWS_REGION="${AWS_REGION:-us-east-2}"
OAUTH_PORTS="${OAUTH_PORTS:-3118,8766}"
INSTANCE_ID_FILE="${JARVIS_INSTANCE_ID_FILE:-.make/instance_id}"

AWS_PROFILE_OPT=()
[ -n "${AWS_PROFILE:-}" ] && AWS_PROFILE_OPT=(--profile "$AWS_PROFILE")

log() { printf '[oauth-login] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

case "$MODE" in
  work | personal) ;;
  *) fail "usage: scripts/oauth-login.sh <work|personal>" ;;
esac

require_tools() {
  command -v aws >/dev/null 2>&1 || fail "aws CLI not found"
  command -v session-manager-plugin >/dev/null 2>&1 \
    || fail "session-manager-plugin not found (brew install --cask session-manager-plugin)"
  aws sts get-caller-identity --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" >/dev/null 2>&1 \
    || fail "no active AWS SSO session (aws sso login) for AWS_PROFILE=${AWS_PROFILE:-<unset>}"
}

instance_id() {
  [ -f "$INSTANCE_ID_FILE" ] || fail "missing $INSTANCE_ID_FILE (run 'make deploy' or 'make status' once first)"
  cat "$INSTANCE_ID_FILE"
}

ssm_params_file() {
  # Writes {"<key>": ["<value>"]} to a fresh mktemp path and prints that path -- the SSM
  # Parameters API is map<string, list<string>> regardless of whether the document itself
  # declares the parameter String or StringList, so a single-element list is always correct.
  local key="$1" value="$2" f
  f="$(mktemp)"
  python3 -c 'import json, sys; print(json.dumps({sys.argv[1]: [sys.argv[2]]}))' "$key" "$value" >"$f"
  printf '%s' "$f"
}

PORT_FORWARD_PIDS=()
start_port_forwards() {
  local id="$1" port params_file log_file
  IFS=',' read -ra ports <<<"$OAUTH_PORTS"
  for port in "${ports[@]}"; do
    [ -n "$port" ] || continue
    log "forwarding port $port to the instance"
    params_file="$(mktemp)"
    python3 -c 'import json, sys; print(json.dumps({"portNumber": [sys.argv[1]], "localPortNumber": [sys.argv[1]]}))' \
      "$port" >"$params_file"
    log_file="$(mktemp)"
    aws ssm start-session --target "$id" --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" \
      --document-name AWS-StartPortForwardingSession --parameters "file://$params_file" \
      >"$log_file" 2>&1 &
    PORT_FORWARD_PIDS+=("$!")
    rm -f -- "$params_file"
  done
  sleep 2
}

stop_port_forwards() {
  local pid
  for pid in "${PORT_FORWARD_PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
}
trap stop_port_forwards EXIT

print_instructions() {
  cat <<EOF

== $MODE: manual steps in the shell that is about to open ==
1. Run: claude
2. Run: /mcp
3. Authorize each server listed (a browser opens for OAuth servers; ports forwarded: $OAUTH_PORTS).
   If a server's browser redirect never arrives, try: claude mcp login <name> --no-browser
EOF
  if [ "$MODE" = "personal" ]; then
    cat <<EOF
4. Also run (env vars first, so GMAIL_WATCH_CLIENT_SECRETS etc. are set -- R5: read line by line,
   never "source"/". ", so a value containing shell metacharacters is neither mangled nor executed):
   while IFS='=' read -r k v; do [ -n "\$k" ] && export "\$k=\$v"; done < ~/.jarvis/env
   /opt/jarvis/current/.venv/bin/python -m jarvis.gmail_watch --auth
EOF
  fi
  cat <<EOF
Exit the shell (Ctrl-D) when every server shows authorized in /mcp.
EOF
}

open_vault_shell() {
  # H4(b): mode users have shell /usr/sbin/nologin (DESIGN.md AD17); `sudo -u ... -i` reads that
  # as the target's login shell and refuses to run at all. `-H bash -lc '...'` sidesteps it by
  # invoking bash explicitly (just setting HOME), never the account's own configured shell.
  local id="$1" params_file
  params_file="$(ssm_params_file command "sudo -u jarvis-$MODE -H bash -lc 'cd ~/vault && exec bash'")"
  aws ssm start-session --target "$id" --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" \
    --document-name AWS-StartInteractiveCommand --parameters "file://$params_file"
  rm -f -- "$params_file"
}

strip_session_banner() {
  # The AWS CLI's own start-session banner lines, not part of the remote command's own output.
  grep -Ev '^(Starting session with SessionId|Exiting session with sessionId)' || true
}

verify_connection() {
  # R2: JarvisOperator does not grant AWS-RunShellScript/send-command; this runs the same way
  # sync-agents.sh does, entirely inside one AWS-StartInteractiveCommand session. R2/R1: that
  # document always reports the SESSION's own exit status, not the remote command's -- an
  # explicit "__rc=<N>" marker (R1's exact form) is appended and parsed from the output instead
  # of trusting `aws ssm start-session`'s own exit code or polling get-command-invocation.
  # R5: reads ~/.jarvis/env line by line rather than "source"/". "-ing it (H4(b)/(c) pattern:
  # claude may need ANTHROPIC_API_KEY or similar from it, AD23).
  local id="$1" remote_cmd params_file raw rc output marker
  # shellcheck disable=SC2016  # deliberately single-quoted: $k/$v/$? must reach the REMOTE shell
  # unexpanded, only $MODE (spliced in via the "'"$MODE"'" break) is meant to expand locally.
  remote_cmd='sudo -u jarvis-'"$MODE"' -H bash -lc '\''while IFS="=" read -r k v; do [ -n "$k" ] && export "$k=$v"; done < ~/.jarvis/env; cd ~/vault && claude -p "Which Gmail account am I connected to? Reply with only the address."'\''; printf "__rc=%s\n" "$?"'
  params_file="$(ssm_params_file command "$remote_cmd")"

  log "verifying: which Gmail account is $MODE connected to?"
  if raw="$(aws ssm start-session --region "$AWS_REGION" "${AWS_PROFILE_OPT[@]}" --target "$id" \
       --document-name AWS-StartInteractiveCommand --parameters "file://$params_file" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  rm -f -- "$params_file"

  output="$(printf '%s\n' "$raw" | strip_session_banner)"
  # `|| true` on every grep here: it exits 1 when nothing matches (marker genuinely absent, or
  # the body is empty once the marker line is excluded), which under `set -e` would otherwise
  # abort the whole script right here -- before the explicit check below, or the caller's own
  # `|| true` on this function, ever get a chance to run.
  marker="$(printf '%s\n' "$output" | grep -o '__rc=[0-9]*' | tail -n 1)" || true
  if [ "$rc" -ne 0 ] || [ "$marker" != "__rc=0" ]; then
    log "verification failed (session exit $rc, marker '${marker:-none}'); check manually with /mcp. Output:"
    printf '%s\n' "$output" | grep -v '^__rc=' >&2 || true
    return 1
  fi
  log "connected Gmail account ($MODE): $(printf '%s\n' "$output" | grep -v '^__rc=' || true)"
}

main() {
  require_tools
  local id
  id="$(instance_id)"
  start_port_forwards "$id"
  print_instructions
  open_vault_shell "$id"
  stop_port_forwards
  # Advisory only: the human already did the important, interactive part above. A failed
  # verification is logged clearly but never turns an otherwise-successful oauth-login run into
  # a non-zero exit.
  verify_connection "$id" || true
}

main
