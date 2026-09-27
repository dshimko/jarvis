#!/usr/bin/env bash
# scripts/ofw-companion-token.sh: creates or rotates OFW Companion's read-only ofw-mcp bearer
# token end to end, without the raw token or its full sha256 ever being printed, logged, or
# written to disk. infra/PLAN.md AD34; infra/RUNBOOK.md section 3 / 3.1.
#
# Steps: generate a token, hash it, merge the hash into the jarvis/ofw secret (refusing when that
# secret has no version yet -- the human must populate OFW credentials first, RUNBOOK section 3),
# and hand the raw token to OFW Companion.
#
# Profile (gate fix, 2026-09-27): `JarvisOperator` -- the profile every other target in this
# Makefile section uses -- can only `PutSecretValue`/`DescribeSecret` on value secrets, never
# `GetSecretValue` (infra/org/policies.tf, statement "PutValueSecrets"; the read grant belongs to
# a different permission set, `JarvisClient`, scoped to the token secrets only). Reading and
# merging jarvis/ofw therefore cannot run under JarvisOperator at all. This script and its
# `make ofw-companion-token` target run under `PROD_AWS_PROFILE` instead -- the same profile
# `make plan` uses: populating jarvis/ofw is an admin step, not a day-to-day deploy operation.
# `AWS_PROFILE` is read from the environment as-is (never defaulted here); the Makefile sets it.
#
# Every aws call's stderr is captured (never discarded to /dev/null): a failure is classified
# before deciding what to print. "no secret version yet" is used only when the failure is
# unambiguously ResourceNotFoundException (or the call succeeded with an empty/None
# SecretString -- the same "Terraform created it empty" shape). Any other failure (AccessDenied,
# an expired SSO token, a network error, ...) prints `FAIL: <operation> failed: <ErrorClass>`,
# where <ErrorClass> is the bracketed exception name parsed out of the CLI's stderr (e.g.
# "AccessDeniedException"), or the fixed label "CLIError" when the CLI's own error text carries no
# such class (an expired SSO token, for example) -- never the stderr's full text, which can name
# ARNs, principals, or other detail this script has no business printing.
#
# Companion's own `make ofw-auth` (services/scheduler/bin/ofw-auth.ts in the Companion repo) has
# no non-interactive path: it prompts on a real TTY (readline for the URL, a raw-stdin
# character-at-a-time reader for the hidden token) and reads no env var or flag. Piping the token
# to it does not merely fail closed -- ofw-auth.ts's hidden-token reader compares each stdin
# `data` chunk for exact equality with a single "\r" or "\n" byte, which is how a TTY delivers
# keystrokes one at a time; a non-TTY pipe instead delivers the whole line (token + newline) as
# one chunk, so that comparison never matches and the process hangs forever waiting for a
# newline it will never see character-by-character. So this script never drives `make ofw-auth`;
# per the deploy-engineer brief it writes directly to the same macOS Keychain service/account
# names ofw-auth.ts itself uses (`ofw-companion-ofw-mcp` / `url`, `token`), with
# `security -i` (interactive mode, reading `add-generic-password -U ...` lines) so the raw token
# never appears as a `security` argv element (visible in `ps`). Gate fix (2026-09-27): that stdin
# stream is never a here-document -- on bash before 5.1 (this includes macOS's `/bin/bash` 3.2), a
# here-document (or here-string) is implemented by writing its content to an unlinked-after-open
# but briefly-real 0600 temp file under `$TMPDIR`, so feeding `security -i` that way would put the
# token on disk after all. Instead a `printf` builtin (never a temp file) pipes the two command lines
# straight into `security -i`'s stdin. Because that stream is not shell-parsed by `security`, its
# values carry no quoting (every one of them is a fixed alphanumeric string, a hex token, or a URL
# with no spaces -- validated below); `security -i`'s own exit status is not trusted either (it
# does not reliably propagate a single command's failure), so a `security find-generic-password`
# lookup (never `-w`, so it prints nothing) verifies the write afterward. macOS only: this script
# refuses on any other platform and points at the manual `make ofw-auth` fallback instead.
#
# Rotation order: this script only updates jarvis/ofw and the workstation's own Keychain. The box
# keeps the old hash (and Companion keeps getting HTTP 401) until `make secrets-sync` re-reads
# jarvis/ofw and rewrites /home/jarvis-ofw/.jarvis/env -- see the fixed line this script prints at
# the end, and infra/RUNBOOK.md section 3.
#
# Region-deny SCP consequence (PLAN.md 3.2 / CLAUDE.md): every aws call passes --region $REGION
# explicitly (REGION defaults to us-east-1; AD42).
#
# Options: COMPANION_DIR (default ~/Documents/sparko/divorceCommunications), OFW_MCP_URL (default
# http://jarvis:8783/mcp), AWS_PROFILE (repo convention: set by the caller/Makefile, not defaulted
# here -- see the profile note above), AWS_REGION (default us-east-1).
set -euo pipefail

COMPANION_DIR="${COMPANION_DIR:-$HOME/Documents/sparko/divorceCommunications}"
OFW_MCP_URL="${OFW_MCP_URL:-http://jarvis:8783/mcp}"
REGION="${AWS_REGION:-us-east-1}"
SECRET_ID="jarvis/ofw"
HASH_KEY="OFW_MCP_COMPANION_TOKEN_SHA256"

# Matches divorceCommunications/services/scheduler/src/credential-relay/mcp-config.ts exactly.
KEYCHAIN_SERVICE="ofw-companion-ofw-mcp"
KEYCHAIN_ACCOUNT_URL="url"
KEYCHAIN_ACCOUNT_TOKEN="token"

SCRATCH_DIR=""

log() { printf '[ofw-companion-token] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

# shellcheck disable=SC2329  # invoked indirectly via `trap cleanup EXIT`
cleanup() {
  local ec=$?
  [ -z "$SCRATCH_DIR" ] || rm -rf -- "$SCRATCH_DIR"
  return "$ec"
}
trap cleanup EXIT

require_prereqs() {
  command -v aws >/dev/null 2>&1 || fail "aws CLI not found on PATH"
  command -v openssl >/dev/null 2>&1 || fail "openssl not found on PATH"
  [ "$(uname -s)" = "Darwin" ] || fail "Companion token storage is only automated on macOS by this script; run 'make ofw-auth' by hand in $COMPANION_DIR and paste the token when prompted (see the header comment above for why it cannot be driven non-interactively)"
  command -v security >/dev/null 2>&1 || fail "security (macOS Keychain CLI) not found on PATH"
  validate_ofw_mcp_url
}

# validate_ofw_mcp_url: refuses (before any AWS call or Keychain write) an OFW_MCP_URL containing
# whitespace (which also catches a newline -- POSIX [:space:] includes it) or a quote character.
# store_companion_token's printf stream has no shell quoting of its own, so a value shaped like
# that could otherwise be read by `security -i` as more than one token or one more line than
# intended.
validate_ofw_mcp_url() {
  case "$OFW_MCP_URL" in
    *[[:space:]]*|*\"*|*\'*)
      fail "OFW_MCP_URL must not contain whitespace, a quote character, or a newline"
      ;;
  esac
}

# parse_error_class <stderr_text>: prints the bracketed AWS exception name out of a CLI error
# ("An error occurred (AccessDeniedException) when calling ..." -> "AccessDeniedException"), or
# the fixed label "CLIError" when no such bracket is present (an expired SSO token, a network
# failure, ...) -- never the surrounding text.
parse_error_class() {
  local stderr_text="$1" class
  class="$(printf '%s' "$stderr_text" | grep -oE '\([A-Za-z][A-Za-z0-9]*\)' | head -n1 || true)"
  class="${class#\(}"
  class="${class%\)}"
  [ -n "$class" ] || class="CLIError"
  printf '%s' "$class"
}

# fetch_current_secret: prints the current jarvis/ofw SecretString on stdout, stderr passed
# through untouched to whatever fd the caller redirects it to. Never calls fail() itself -- see
# update_secrets_manager, which is the only caller and always captures this via a plain command
# substitution (no fail() runs inside that capture).
fetch_current_secret() {
  aws secretsmanager get-secret-value \
    --region "$REGION" \
    --secret-id "$SECRET_ID" \
    --query SecretString \
    --output text
}

# merge_hash_into_json <hash> <out_file>: reads the current secret JSON from stdin (a pipe, never
# argv -- CLAUDE.md pitfall), merges in $HASH_KEY=<hash>, and writes the merged JSON to out_file.
# The hash and key name travel through env vars rather than argv so they never appear in a
# process listing. Uses `python3 -c` (not `python3 -` fed via a here-document): a here-document
# would redirect fd 0 to the program text itself, leaving nothing on stdin for json.load to read
# (and, per the gate fix above, would itself be a temp-file-backed stream). Exits non-zero with no
# message at all (never a traceback, which could echo a fragment of the secret) when the input is
# not valid JSON or not a JSON object; the caller turns that into one fixed refusal line.
merge_hash_into_json() {
  local hash="$1" out_file="$2"
  OFW_COMPANION_HASH="$hash" OFW_HASH_KEY="$HASH_KEY" python3 -c '
import json
import os
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(2)
if not isinstance(data, dict):
    sys.exit(2)
data[os.environ["OFW_HASH_KEY"]] = os.environ["OFW_COMPANION_HASH"]
try:
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(data, fh)
except Exception:
    sys.exit(3)
' "$out_file" 2>/dev/null
}

update_secrets_manager() {
  local hash="$1" current_json tmp_file
  local get_stderr_file get_status stderr_text class
  local put_stderr_file put_status

  get_stderr_file="$SCRATCH_DIR/get-secret-value.stderr"
  set +e
  current_json="$(fetch_current_secret 2>"$get_stderr_file")"
  get_status=$?
  set -e

  if [ "$get_status" -ne 0 ]; then
    stderr_text="$(cat "$get_stderr_file" 2>/dev/null || true)"
    if printf '%s' "$stderr_text" | grep -q "ResourceNotFoundException"; then
      fail "$SECRET_ID has no secret version yet; populate OFW credentials first (infra/RUNBOOK.md section 3), then re-run this"
    fi
    class="$(parse_error_class "$stderr_text")"
    fail "get-secret-value failed: $class"
  fi

  if [ -z "$current_json" ] || [ "$current_json" = "None" ]; then
    fail "$SECRET_ID has no secret version yet; populate OFW credentials first (infra/RUNBOOK.md section 3), then re-run this"
  fi

  tmp_file="$SCRATCH_DIR/secret.json"
  : >"$tmp_file"
  chmod 600 "$tmp_file"

  if ! printf '%s' "$current_json" | merge_hash_into_json "$hash" "$tmp_file"; then
    fail "$SECRET_ID secret is not valid JSON (or not a JSON object); refusing before any write"
  fi

  put_stderr_file="$SCRATCH_DIR/put-secret-value.stderr"
  set +e
  aws secretsmanager put-secret-value \
    --region "$REGION" \
    --secret-id "$SECRET_ID" \
    --secret-string "file://$tmp_file" >/dev/null 2>"$put_stderr_file"
  put_status=$?
  set -e

  if [ "$put_status" -ne 0 ]; then
    class="$(parse_error_class "$(cat "$put_stderr_file" 2>/dev/null || true)")"
    fail "put-secret-value failed: $class"
  fi

  log "updated secret $SECRET_ID (region $REGION)"
}

store_companion_token() {
  local tok="$1"
  log "ofw-auth.ts supports only an interactive hidden prompt (see header); writing directly to the Companion Keychain entries in $COMPANION_DIR's ofw-auth instead"

  # A builtin `printf`, piped -- never a here-document/here-string (see the header comment: those
  # are temp-file-backed on bash < 5.1). No quoting around the values: this stream is not
  # shell-parsed, and validate_ofw_mcp_url plus the fixed/hex-only values above rule out anything
  # `security -i`'s own tokenizer could misread. `security -i`'s exit status is ignored (`|| true`)
  # since it is not a reliable per-command signal; the find-generic-password lookup below is.
  { printf 'add-generic-password -U -s %s -a %s -w %s\n' "$KEYCHAIN_SERVICE" "$KEYCHAIN_ACCOUNT_URL" "$OFW_MCP_URL"
    printf 'add-generic-password -U -s %s -a %s -w %s\n' "$KEYCHAIN_SERVICE" "$KEYCHAIN_ACCOUNT_TOKEN" "$tok"
  } | security -i >/dev/null 2>&1 || true

  # Verifies the write landed without ever printing the secret back (no -w here).
  if ! security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT_TOKEN" >/dev/null 2>&1; then
    fail "secret updated but the Keychain write failed; re-run make ofw-companion-token"
  fi

  log "stored Companion token in the macOS Keychain (service=$KEYCHAIN_SERVICE, account=$KEYCHAIN_ACCOUNT_TOKEN)"
}

main() {
  require_prereqs
  SCRATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-ofw-companion-token.XXXXXX")"
  chmod 700 "$SCRATCH_DIR"

  local tok hash
  tok="$(openssl rand -hex 32)"
  hash="$(printf %s "$tok" | openssl dgst -sha256 -r | cut -d' ' -f1)"

  update_secrets_manager "$hash"
  store_companion_token "$tok"

  log "token hash starts with ${hash:0:8} (cross-check: AWS_PROFILE=${AWS_PROFILE:-<unset>} aws secretsmanager get-secret-value --secret-id $SECRET_ID --region $REGION --query SecretString --output text)"
  log "now run: make secrets-sync (the box keeps the old hash until then; Companion gets 401 meanwhile)"
}

main "$@"
