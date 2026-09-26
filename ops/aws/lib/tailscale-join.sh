#!/usr/bin/env bash
# Reads the Tailscale auth key from Secrets Manager into a tmpfs file and joins the tailnet.
# infra/DESIGN.md section 5, "Tailscale key tmpfs handling". The key value travels by pipe only:
# never argv, never an env var, never a shell variable holding the plaintext (M6/L5 tightened:
# the fetched key is tested by inspecting the file itself, and umask is scoped to a subshell so
# it never leaks into the rest of bootstrap.sh's process).

tailscale_join() {
  local secret_id="$1" region="$2"
  local keyfile="/run/jarvis/ts-authkey"

  if [ "$(tailscale status --json 2>/dev/null | jq -r '.BackendState // empty')" = "Running" ]; then
    log "tailscale already joined, skipping"
    return 0
  fi

  ( umask 077 && install -d -m 0700 -o root -g root /run/jarvis )
  trap 'shred -u "'"$keyfile"'" 2>/dev/null || rm -f "'"$keyfile"'"' EXIT

  local waited=0
  while :; do
    ( umask 077
      aws secretsmanager get-secret-value --secret-id "$secret_id" --region "$region" \
        --query SecretString --output text 2>/dev/null | jq -r .authkey > "$keyfile" ) || true
    # Non-empty (-s) and at least one line that is not the literal string "null" (grep -qv):
    # the key's content is never read into a shell variable to make this check.
    if [ -s "$keyfile" ] && grep -qv '^null$' "$keyfile"; then
      break
    fi
    if [ "$waited" -ge 1800 ]; then
      log "tailscale_key_missing"
      rm -f "$keyfile"
      return 1
    fi
    sleep 30
    waited=$((waited + 30))
  done

  tailscale up --auth-key="file:$keyfile" --advertise-tags=tag:jarvis --hostname=jarvis \
    --ssh=false --accept-dns=false

  shred -u "$keyfile" 2>/dev/null || rm -f "$keyfile"
  trap - EXIT
}
