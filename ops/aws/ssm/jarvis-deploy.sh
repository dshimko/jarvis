#!/usr/bin/env bash
# jarvis-deploy: the SSM `jarvis-deploy` document body (Sha parameter -> SHA env var). Downloads a
# release, verifies its checksum, builds the venv, runs the test suite as an unprivileged user,
# installs ops/aws/ from the release, atomically switches /opt/jarvis/current, restarts both
# daemons, and health-checks each -- rolling back to the previous release on any failure from the
# ops/aws/ install step onward. infra/DESIGN.md sections 8.2/8.3; PLAN.md AD19, AD31.
#
# PLAN.md AD40: when the release also ships ofw-mcp/ (the ofw-mcp wheel + hash-pinned
# requirements-lock.txt + tests, built by scripts/release.sh), this also builds a second venv
# (ofw-venv) under the same release directory, installs the bundled Chromium (system deps as
# root, the browser itself as jarvis-ofw, AD31), and runs the ofw-mcp suite (-m "not browser") as
# jarvis-build. ofw-mcp.service is restarted alongside the two mode units and, once its own
# ConditionPathExists checks pass (the binary exists AND the human has populated jarvis/ofw),
# health-checked over its loopback /healthz; a release without ofw-mcp/, or a box where the human
# has not populated secrets yet, skips all of that with one log line and is never a health
# failure. The one /opt/jarvis/current symlink covers both venvs, so rollback restores both at
# once; rollback also restarts ofw-mcp.service (via restart_daemons(), below).
#
# All paths derive from JARVIS_ROOT (default /opt/jarvis) so tests can point it at a temp dir. A
# few paths are never under JARVIS_ROOT even in production (systemd units, iptables rules, the
# CloudWatch agent config, logrotate, and the mode users' home directories); those have their own
# JARVIS_*_DIR overrides for the same reason.
#
# SHA comes from the `SHA` env var (the SSM document sets this from its `Sha` parameter) or, as a
# fallback, from $1 (ops/aws/lib/first-deploy.sh's call convention: `jarvis-deploy "$sha"`). The
# release identity is the full 40-character git sha everywhere (release.sh, the S3 layout, and
# this script's own re-validation below), matching the SSM document's
# `allowedPattern ^[0-9a-f]{40}$`; `make deploy SHA=<short-or-full>` resolves a short sha to the
# full one locally before sending the command, so this script itself only ever sees a full sha.
#
# Root-owned files: every `install` call below is deliberately missing an explicit -o/-g -- this
# script always runs as root (an SSM document, or first-deploy.sh at boot), so files it creates
# are root-owned by virtue of the effective uid, without needing a portable `-o root -g root` that
# would fail under a non-root test run.
#
# Errexit and conditionals: bash suspends `set -e` for the ENTIRE body of a function (and anything
# it calls) whenever that function is invoked as part of an `if`/`while`/`&&`/`||` condition. main()
# below therefore never chains the deploy steps in one `if A && B && C; then`; each step is called
# as its own statement and checked explicitly, and every function that might still end up called
# from a conditional context (install_release_ops_aws, wait_for_health) does its own internal
# `|| return 1` bookkeeping rather than depending on ambient errexit.
set -euo pipefail

JARVIS_ROOT="${JARVIS_ROOT:-/opt/jarvis}"
SHA="${SHA:-${1:-}}"
INSTANCE_ENV_FILE="${JARVIS_INSTANCE_ENV_FILE:-/etc/jarvis/instance.env}"
if [ -z "${JARVIS_ARTIFACTS_BUCKET:-}" ] && [ -f "$INSTANCE_ENV_FILE" ]; then
  # Only ever picks up the two keys we need; never sources the whole file blindly.
  eval "$(grep -E '^JARVIS_(ARTIFACTS_BUCKET|REGION)=' "$INSTANCE_ENV_FILE")"
fi
ARTIFACTS_BUCKET="${JARVIS_ARTIFACTS_BUCKET:?JARVIS_ARTIFACTS_BUCKET must be set (env or /etc/jarvis/instance.env)}"
# AD42: region literals leave every script. JARVIS_REGION (env) wins; otherwise fall back to the
# file user_data.sh.tftpl writes before bootstrap.sh runs (JARVIS_REGION_FILE override for tests).
# Fails loudly if both are empty.
REGION_FILE="${JARVIS_REGION_FILE:-/etc/jarvis/region}"
# `|| true`: under `set -e`, a failing `cat` (missing file) inside this command substitution
# would otherwise abort the script right here, before the explicit `:?` check below ever runs.
AWS_REGION="${JARVIS_REGION:-$(cat "$REGION_FILE" 2>/dev/null || true)}"
: "${AWS_REGION:?AWS region not set: export JARVIS_REGION or ensure $REGION_FILE exists}"

RELEASES_DIR="$JARVIS_ROOT/releases"
CURRENT_LINK="$JARVIS_ROOT/current"
BIN_DIR="$JARVIS_ROOT/bin"
LIBEXEC_DIR="$JARVIS_ROOT/libexec"
SYSTEMD_DIR="${JARVIS_SYSTEMD_DIR:-/etc/systemd/system}"
IPTABLES_DIR="${JARVIS_IPTABLES_DIR:-/etc/iptables}"
CW_CONFIG_DIR="${JARVIS_CW_CONFIG_DIR:-/opt/aws/amazon-cloudwatch-agent/etc}"
LOGROTATE_DIR="${JARVIS_LOGROTATE_DIR:-/etc/logrotate.d}"
HOME_DIR="${JARVIS_HOME_DIR:-/home}"
RUN_DIR="${JARVIS_RUN_DIR:-/run/jarvis}"                      # tmpfs; health-check header file
LOCK_FILE="${JARVIS_DEPLOY_LOCK:-/run/jarvis-deploy.lock}"

PORT_WORK="${JARVIS_PORT_WORK:-8781}"
PORT_PERSONAL="${JARVIS_PORT_PERSONAL:-8782}"
KEEP_RELEASES="${JARVIS_KEEP_RELEASES:-5}"
HEALTH_TIMEOUT_S="${JARVIS_HEALTH_TIMEOUT_S:-60}"
HEALTH_INTERVAL_S="${JARVIS_HEALTH_INTERVAL_S:-2}"
SHA_RE='^[0-9a-f]{40}$'  # matches the SSM document's Sha allowedPattern exactly

STAGING_DIR=""
TEST_HOME=""
HDR_FILE=""

log() { printf '[jarvis-deploy] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

# shellcheck disable=SC2329  # invoked indirectly via `trap cleanup EXIT` below
cleanup() {
  local ec=$?
  [ -z "$STAGING_DIR" ] || rm -rf -- "$STAGING_DIR"
  [ -z "$TEST_HOME" ] || rm -rf -- "$TEST_HOME"
  [ -z "$HDR_FILE" ] || rm -f -- "$HDR_FILE"
  return "$ec"
}
trap cleanup EXIT

port_for() { [ "$1" = work ] && printf '%s' "$PORT_WORK" || printf '%s' "$PORT_PERSONAL"; }

acquire_lock() {
  if ! command -v flock >/dev/null 2>&1; then
    log "flock not available on this host, skipping the deploy lock"
    return 0
  fi
  exec 9>"$LOCK_FILE"
  flock -n 9 || fail "another jarvis-deploy is already running ($LOCK_FILE)"
}

download_and_verify() {
  STAGING_DIR="$(mktemp -d "$RELEASES_DIR/.staging-$SHA.XXXXXX")"
  local tgz="jarvis-$SHA.tar.gz"
  aws s3 cp "s3://$ARTIFACTS_BUCKET/releases/$SHA/$tgz" "$STAGING_DIR/$tgz" \
    --region "$AWS_REGION" --only-show-errors
  aws s3 cp "s3://$ARTIFACTS_BUCKET/releases/$SHA/$tgz.sha256" "$STAGING_DIR/$tgz.sha256" \
    --region "$AWS_REGION" --only-show-errors
  (cd "$STAGING_DIR" && sha256_check "$tgz.sha256") || fail "checksum mismatch for $tgz, touching nothing"
}

sha256_check() {
  # sha256sum is GNU-only (not on macOS); shasum -a 256 reads the same "<hex>  <name>" format and
  # is the portable fallback, used whenever sha256sum isn't on PATH.
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "$1"
  else
    shasum -a 256 -c "$1"
  fi
}

ensure_build_user() {
  id -u jarvis-build >/dev/null 2>&1 && return 0
  if command -v useradd >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin jarvis-build
  else
    log "useradd unavailable; assuming jarvis-build already exists (non-Linux test host)"
  fi
}

build_and_test() {
  # M4: hash-pinned install. requirements-lock.txt (uv pip compile --generate-hashes, includes
  # requirements.txt via requirements-dev.txt's own -r line) ships in every release; --require-hashes
  # means pip refuses to install anything not exactly pinned-and-hashed there.
  local release_dir="$1"
  log "building venv in $release_dir"
  python3.12 -m venv "$release_dir/.venv"
  "$release_dir/.venv/bin/pip" install --quiet --require-hashes -r "$release_dir/requirements-lock.txt"

  ensure_build_user
  TEST_HOME="$(mktemp -d)"
  chown jarvis-build:jarvis-build "$TEST_HOME"
  log "running pytest as jarvis-build (HOME=$TEST_HOME)"
  if ! ( cd "$release_dir" && runuser -u jarvis-build -- env HOME="$TEST_HOME" JARVIS_DEPLOYMENT=local \
           "$release_dir/.venv/bin/pytest" -q ); then
    fail "test suite failed for $SHA; current left unchanged"
  fi
  rm -rf -- "$TEST_HOME"
  TEST_HOME=""
}

# AD40: ofw-venv, sibling to the Jarvis .venv under the same release directory. `ensure_build_user`
# is already called by build_and_test(); calling it again here is a harmless idempotent recheck
# (id -u), kept so this function stands on its own.
#
# Gate fix (item 2): a live box bootstrapped before phase I has no jarvis-ofw user yet.
# install_release_ops_aws (which also sources lib/users.sh and calls setup_users) does not run
# until AFTER unpack_release in main(), so this function ensures jarvis-ofw exists itself, before
# its own first `runuser -u jarvis-ofw` step -- never assumes install_release_ops_aws already ran.
ensure_ofw_user() {
  local users_lib="$1/ops/aws/lib/users.sh"
  [ -f "$users_lib" ] || fail "release $SHA has no ops/aws/lib/users.sh; cannot ensure jarvis-ofw exists"
  # shellcheck source=/dev/null
  . "$users_lib"
  setup_users || fail "setup_users failed for $SHA; current left unchanged"
}

build_and_test_ofw() {
  local release_dir="$1" ofw_dir="$1/ofw-mcp" venv="$1/ofw-venv" wheel wheel_count
  log "building ofw-mcp venv in $venv"
  python3.12 -m venv "$venv"
  "$venv/bin/pip" install --quiet --require-hashes -r "$ofw_dir/requirements-lock.txt"

  wheel_count="$(find "$ofw_dir/wheels" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l | tr -d '[:space:]')"
  [ "$wheel_count" = "1" ] || fail "expected exactly one ofw-mcp wheel in $ofw_dir/wheels, found $wheel_count"
  wheel="$(find "$ofw_dir/wheels" -maxdepth 1 -name '*.whl')"
  "$venv/bin/pip" install --quiet --no-deps "$wheel"

  log "installing chromium system dependencies as root"
  "$venv/bin/playwright" install-deps chromium \
    || fail "playwright install-deps chromium failed for $SHA; current left unchanged"

  ensure_ofw_user "$release_dir"

  # AD31: the browser itself is installed as jarvis-ofw (never root) into that user's own cache.
  log "installing chromium as jarvis-ofw"
  runuser -u jarvis-ofw -- env HOME=/home/jarvis-ofw \
    PLAYWRIGHT_BROWSERS_PATH=/home/jarvis-ofw/.cache/ms-playwright \
    "$venv/bin/playwright" install chromium \
    || fail "playwright install chromium failed for $SHA; current left unchanged"

  ensure_build_user
  TEST_HOME="$(mktemp -d)"
  chown jarvis-build:jarvis-build "$TEST_HOME"
  log "running ofw-mcp pytest as jarvis-build (HOME=$TEST_HOME)"
  # Gate fix (item 1, PLAN.md AD40 amendment): the shipped tests/ + pyproject.toml + lock are
  # self-contained (deps/ofw-mcp.sha's own contract, see deps/README.md); ofw-mcp's own "repo"
  # marker covers any test that imports from its scripts/ or reads outside tests/, so it is
  # excluded here alongside "browser" and "live" (item 13: "not live" was already implied by
  # nothing here ever setting OFW_LIVE=1, now made explicit in the marker expression itself).
  if ! ( cd "$release_dir" && runuser -u jarvis-build -- env HOME="$TEST_HOME" \
           PYTHONDONTWRITEBYTECODE=1 "$venv/bin/pytest" -q -m "not browser and not live and not repo" \
           -p no:cacheprovider -c "$ofw_dir/pyproject.toml" "$ofw_dir/tests" ); then
    fail "ofw-mcp test suite failed for $SHA; current left unchanged"
  fi
  rm -rf -- "$TEST_HOME"
  TEST_HOME=""
}

unpack_release() {
  local release_dir="$RELEASES_DIR/$SHA"
  if [ -f "$release_dir/.complete" ]; then
    log "release $SHA already unpacked and tested, skipping build"
    return 0
  fi
  install -d -m 0755 "$release_dir"
  tar -xzf "$STAGING_DIR/jarvis-$SHA.tar.gz" -C "$release_dir"
  build_and_test "$release_dir"
  if [ -d "$release_dir/ofw-mcp" ]; then
    build_and_test_ofw "$release_dir"
  else
    log "release $SHA has no ofw-mcp/, skipping ofw-mcp packaging"
  fi
  touch "$release_dir/.complete"
}

current_target() {
  [ -L "$CURRENT_LINK" ] && basename "$(readlink "$CURRENT_LINK")" || true
}

# M3: the actual file-copying logic lives in ops/aws/lib/install-release.sh, shared with any
# future bootstrap-side caller; sourced from the RELEASE being installed (ops/aws/ ships with
# every release, PLAN.md AD18), never a fixed path. M2: never called from inside an if/&&/||
# condition in main() -- see the header comment -- and never calls `fail` (which exits the whole
# script), only `return 1`, so rollback's `|| true` pattern can log and continue past it.
install_release_ops_aws() {
  local sha="$1" src="$RELEASES_DIR/$1/ops/aws"
  if [ ! -f "$src/lib/install-release.sh" ]; then
    log "release $sha has no ops/aws/lib/install-release.sh"
    return 1
  fi
  # shellcheck source=/dev/null
  . "$src/lib/install-release.sh"
  install_release_files "$src" "$BIN_DIR" "$LIBEXEC_DIR" "$SYSTEMD_DIR" "$IPTABLES_DIR" \
    "$CW_CONFIG_DIR" "$LOGROTATE_DIR"
}

switch_current() {
  # Atomic, portable symlink swap. DESIGN.md's literal recipe is `mv -T` (GNU-only: BSD/macOS mv
  # has no -T and would move current.new *into* an existing directory-like target instead of
  # replacing it). `os.replace` calls rename(2), which POSIX guarantees replaces the destination
  # atomically without dereferencing it -- a symlink at $CURRENT_LINK is replaced, never followed
  # -- on both Linux and macOS, so this keeps the same atomicity guarantee portably.
  ln -sfn "releases/$1" "$JARVIS_ROOT/current.new"
  python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$JARVIS_ROOT/current.new" "$CURRENT_LINK"
}

restart_daemons() { systemctl restart jarvis@work jarvis@personal ofw-mcp.service; }

wait_for_health() {
  local mode="$1" ip port token deadline resp ok got_mode dep
  ip="$(tailscale ip -4)" || return 1
  port="$(port_for "$mode")"
  token="$(runuser -u "jarvis-$mode" -- cat "$HOME_DIR/jarvis-$mode/.jarvis/api_token" 2>/dev/null)" || return 1
  [ -n "$token" ] || return 1

  # DESIGN.md section 6.2: the deploy header file lives under /run/jarvis (tmpfs, root 0700), not
  # the general system tmp dir, and is named per mode so two concurrent health checks never race
  # on the same path.
  install -d -m 0700 "$RUN_DIR" 2>/dev/null || true
  HDR_FILE="$RUN_DIR/hdr-$mode"
  rm -f -- "$HDR_FILE"
  : >"$HDR_FILE"
  chmod 0600 "$HDR_FILE"
  printf 'Authorization: Bearer %s\n' "$token" >"$HDR_FILE"

  deadline=$(($(date +%s) + HEALTH_TIMEOUT_S))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if resp="$(curl -sf --max-time 5 -H "@$HDR_FILE" "http://$ip:$port/health" 2>/dev/null)"; then
      ok="$(printf '%s' "$resp" | jq -r '.ok // empty' 2>/dev/null)"
      got_mode="$(printf '%s' "$resp" | jq -r '.mode // empty' 2>/dev/null)"
      dep="$(printf '%s' "$resp" | jq -r '.deployment // empty' 2>/dev/null)"
      if [ "$ok" = "true" ] && [ "$got_mode" = "$mode" ] && [ "$dep" = "aws" ]; then
        rm -f -- "$HDR_FILE"
        HDR_FILE=""
        return 0
      fi
    fi
    sleep "$HEALTH_INTERVAL_S"
  done
  rm -f -- "$HDR_FILE"
  HDR_FILE=""
  return 1
}

# check_ofw_health: AD40. Returns 0 (no failure) both when ofw-mcp.service passed its own health
# check AND when it is still condition-skipped (release has no ofw-mcp/, or the human has not yet
# populated jarvis/ofw) -- neither is a deploy failure. Returns 1 only when the unit's own
# conditions passed (it is meant to be running) but /healthz did not answer ok in time.
check_ofw_health() {
  local condition
  condition="$(systemctl show ofw-mcp.service -p ConditionResult 2>/dev/null)"
  if [ "$condition" = "ConditionResult=no" ]; then
    log "ofw_mcp_not_deployed_yet"
    return 0
  fi
  if wait_for_ofw_health; then
    return 0
  fi
  log "ofw-mcp /healthz check failed"
  return 1
}

wait_for_ofw_health() {
  # Loopback, unauthenticated (AD33): root is one of the two uids the iptables owner rule allows
  # through on 8783, so this needs no token, unlike the per-mode /health checks above.
  local deadline resp ok
  deadline=$(($(date +%s) + HEALTH_TIMEOUT_S))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if resp="$(curl -sf --max-time 5 "http://127.0.0.1:8783/healthz" 2>/dev/null)"; then
      ok="$(printf '%s' "$resp" | jq -r 'if (.ok|type)=="boolean" then (.ok|tostring) else empty end' 2>/dev/null)"
      [ "$ok" = "true" ] && return 0
    fi
    sleep "$HEALTH_INTERVAL_S"
  done
  return 1
}

prune_old_releases() {
  # Keeps the $keep most-recent releases by mtime. cur and prev are additionally, explicitly
  # protected even in the (should-never-happen) case their mtime doesn't already rank them there;
  # in the normal case they ARE the two newest directories, so they are already within the top
  # $keep and steady state after enough deploys is exactly $keep releases retained.
  local keep="$1" prev="$2" cur old
  cur="$(current_target)"
  # shellcheck disable=SC2010  # release directory names are opaque shas; mtime order needs ls -t
  ls -1t "$RELEASES_DIR" 2>/dev/null | grep -Ev '^\.' | tail -n "+$((keep + 1))" | while IFS= read -r old; do
    [ -n "$old" ] || continue
    [ "$old" = "$cur" ] && continue
    [ "$old" = "$prev" ] && continue
    rm -rf -- "${RELEASES_DIR:?}/${old:?}"
  done
}

rollback() {
  local prev="$1" health_ok=1
  if [ -z "$prev" ]; then
    log "no previous release to roll back to; stopping both daemons"
    systemctl stop jarvis@work jarvis@personal ofw-mcp.service 2>/dev/null || true
    log "rolled_back none -> stopped"
    return 0
  fi
  log "rolling back $SHA -> $prev"
  install_release_ops_aws "$prev" || true
  switch_current "$prev" || true
  restart_daemons || true
  wait_for_health work || health_ok=0
  wait_for_health personal || health_ok=0
  if [ "$health_ok" -eq 1 ]; then
    log "rolled_back $SHA -> $prev"
  else
    log "rollback_health_check_failed $SHA -> $prev (daemons restarted but /health did not pass; check manually)"
  fi
}

main() {
  [ -n "$SHA" ] || fail "usage: SHA=<sha> jarvis-deploy.sh (or jarvis-deploy.sh <sha>)"
  [[ "$SHA" =~ $SHA_RE ]] || fail "invalid sha '$SHA'"
  [ -d "$RELEASES_DIR" ] || fail "JARVIS_ROOT=$JARVIS_ROOT has no releases/ directory"

  acquire_lock
  download_and_verify
  unpack_release
  rm -rf -- "$STAGING_DIR"
  STAGING_DIR=""

  local prev ok=1
  prev="$(current_target)"

  # M2: each step is its own statement, never chained into one `if A && B && C; then` (that would
  # suspend errexit across all of them, hiding a failure in the middle of, say,
  # install_release_ops_aws until whatever ran last happened to also fail).
  install_release_ops_aws "$SHA" || ok=0
  if [ "$ok" -eq 1 ]; then switch_current "$SHA" || ok=0; fi
  if [ "$ok" -eq 1 ]; then restart_daemons || ok=0; fi
  if [ "$ok" -eq 1 ]; then wait_for_health work || ok=0; fi
  if [ "$ok" -eq 1 ]; then wait_for_health personal || ok=0; fi
  if [ "$ok" -eq 1 ]; then check_ofw_health || ok=0; fi

  if [ "$ok" -eq 1 ]; then
    prune_old_releases "$KEEP_RELEASES" "$prev"
    log "deployed $SHA"
    exit 0
  fi

  rollback "$prev"
  exit 1
}

main "$@"
