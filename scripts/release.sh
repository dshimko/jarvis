#!/usr/bin/env bash
# scripts/release.sh: builds a release tarball of the app + vault templates, verifies it excludes
# secrets, uploads it to the artifacts bucket, and writes releases/latest (informational only --
# not read by any deploy or bootstrap logic; `make deploy` writes the operational
# releases/DEPLOYED marker on success, which ops/aws/lib/first-deploy.sh reads at boot).
# `make release` wraps this. infra/PLAN.md deploy-engineer deliverable 1; infra/DESIGN.md 8.1.
#
# Region-deny SCP consequence (PLAN.md 3.2): every aws call passes --region $REGION explicitly
# (REGION defaults to us-east-1; AD42).
# Bucket key-pinning Deny (PLAN.md 3.2): the artifacts bucket's DenyWrongKmsKey policy rejects an
# upload naming the wrong SSE-KMS key AND one naming none-with-a-null-key-id, so the only
# compliant upload is one with NO server-side-encryption header at all; `aws s3 cp` below never
# passes an --sse flag.
#
# Runs on the workstation (this is the human's Mac), not on the box: uses `shasum -a 256`
# directly rather than jarvis-deploy.sh's sha256sum/shasum fallback, since macOS never has
# sha256sum on PATH.
#
# PLAN.md AD40: packages the ofw-mcp wheel too. deps/ofw-mcp.sha (full 40-char sha, committed like
# any other tracked file -- see deps/README.md) names the ofw-mcp commit to build; OFW_MCP_SRC
# (default ~/code/ofw-mcp) is where it lives. Without deps/ofw-mcp.sha this script refuses unless
# OFW_MCP_SKIP=1, in which case the tarball ships with no ofw-mcp/ directory at all.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
REGION="${AWS_REGION:-us-east-1}"
ARTIFACTS_BUCKET="${ARTIFACTS_BUCKET:-}"
BUILD_DIR="${JARVIS_RELEASE_BUILD_DIR:-$(mktemp -d)}"
OFW_MCP_SRC="${OFW_MCP_SRC:-$HOME/code/ofw-mcp}"
OFW_MCP_SKIP="${OFW_MCP_SKIP:-0}"
# Gate fix (FIX-REQUIRED item 7): overridable so tests never have to write into this real
# checkout; production always gets the deps/ofw-mcp.sha default.
OFW_MCP_SHA_FILE="${JARVIS_OFW_MCP_SHA_FILE:-deps/ofw-mcp.sha}"
OFW_SHA_RE='^[0-9a-f]{40}$'
CLONE_DIR=""
# M4: requirements-lock.txt (uv pip compile --generate-hashes, includes requirements.txt via
# requirements-dev.txt's own -r line) ships alongside the two source files so jarvis-deploy.sh can
# `pip install --require-hashes` from it.
# H1: ops/jarvis@.service and infra/modules/compute/templates/ are read by tests/test_wsl_unit.py
# and tests/test_ops_aws_static.py respectively -- without them, the on-box `pytest -q` inside
# jarvis-deploy.sh's build_and_test step fails on every release. tests/test_release_tree.py
# builds this exact tree and checks it collects cleanly, so a future gap here fails locally too.
ARCHIVE_PATHS="jarvis requirements.txt requirements-dev.txt requirements-lock.txt pytest.ini tests mcp vaults ops/aws ops/jarvis@.service infra/modules/compute/templates scripts"

log() { printf '[release] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

# Gate fix (item 12): cleans up package_ofw_mcp's scratch clone on ANY exit -- success already
# clears CLONE_DIR itself before this ever fires; a failure (fail() calls exit) leaves it set, so
# the trap removes it instead of leaking a clone under BUILD_DIR.
# shellcheck disable=SC2329  # invoked indirectly via `trap cleanup_clone EXIT`
cleanup_clone() {
  local ec=$?
  [ -z "$CLONE_DIR" ] || rm -rf -- "$CLONE_DIR"
  return "$ec"
}
trap cleanup_clone EXIT

require_clean_tree() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "not a git checkout"
  if [ -n "$(git status --porcelain)" ]; then
    if [ "$ALLOW_DIRTY" = "1" ]; then
      log "working tree is dirty; continuing because ALLOW_DIRTY=1"
    else
      fail "working tree is dirty (uncommitted or untracked changes). Commit or stash them, or set ALLOW_DIRTY=1 to release anyway."
    fi
  fi
}

resolve_bucket() {
  # H3: JarvisOperator (the day-to-day profile) cannot read the Terraform state bucket, so the
  # bucket name comes from ARTIFACTS_BUCKET or the jarvis-artifacts-<account-id> naming
  # convention, resolved from a plain `sts get-caller-identity` call instead of `terraform output`.
  if [ -n "$ARTIFACTS_BUCKET" ]; then
    printf '%s' "$ARTIFACTS_BUCKET"
    return 0
  fi
  local account_id
  account_id="$(aws sts get-caller-identity --query Account --output text --region "$REGION" 2>/dev/null)" \
    || fail "set ARTIFACTS_BUCKET, or ensure 'aws sts get-caller-identity' works (need a valid AWS_PROFILE)"
  printf 'jarvis-artifacts-%s' "$account_id"
}

build_tarball() {
  # Archives uncompressed; VERSION (generated, not tracked -- DESIGN.md 8.1) and, conditionally,
  # ofw-mcp/ (built, not tracked -- AD40) are appended before gzip, since `git archive` alone can
  # only emit what's in the committed tree. Caller gzips once every append is done.
  local out="$1" sha="$2" config_files
  config_files="$(git ls-files -- 'config*.yaml')"
  # git archive builds straight from the committed tree at HEAD, so every path below -- vaults/
  # included -- is templates-only by construction: nothing untracked can ever be in it. It also
  # always normalizes entry ownership to uid/gid 0, which the appended VERSION member (below)
  # must match explicitly since it isn't a git-archive entry.
  # shellcheck disable=SC2086  # $config_files/$ARCHIVE_PATHS: intentional word-split filename lists
  git archive --format=tar --output="$out" HEAD -- $config_files $ARCHIVE_PATHS \
    || fail "git archive failed"

  append_version "$out" "$sha"
}

append_version() {
  # python's tarfile (portable: no GNU-vs-BSD tar --owner/--uid flag differences) so the
  # generated VERSION member gets uid=gid=0 like every git-archive entry around it.
  local tar_path="$1" sha="$2"
  python3 - "$tar_path" "$sha" <<'PY'
import io
import sys
import tarfile

tar_path, sha = sys.argv[1], sys.argv[2]
data = (sha + "\n").encode("utf-8")
info = tarfile.TarInfo(name="VERSION")
info.size = len(data)
info.mode = 0o644
info.uid = 0
info.gid = 0
info.uname = ""
info.gname = ""
with tarfile.open(tar_path, "a") as tf:
    tf.addfile(info, io.BytesIO(data))
PY
}

# append_dir <tar_path> <src_dir> <arc_prefix>: appends every file and directory under src_dir
# into the (still uncompressed) tar at tar_path, rooted at arc_prefix, normalizing ownership to
# uid/gid 0 like every other entry (append_version, git archive itself). Used to fold the built
# (not tracked, AD40) ofw-mcp/ tree into the release tarball.
append_dir() {
  local tar_path="$1" src_dir="$2" arc_prefix="$3"
  python3 - "$tar_path" "$src_dir" "$arc_prefix" <<'PY'
import os
import sys
import tarfile

tar_path, src_dir, arc_prefix = sys.argv[1], sys.argv[2], sys.argv[3]
with tarfile.open(tar_path, "a") as tf:
    for root, dirs, files in os.walk(src_dir):
        dirs.sort()
        files.sort()
        rel_root = os.path.relpath(root, src_dir)
        arc_root = arc_prefix if rel_root == "." else f"{arc_prefix}/{rel_root}"
        info = tf.gettarinfo(root, arcname=arc_root)
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mode = 0o755
        tf.addfile(info)
        for name in files:
            full = os.path.join(root, name)
            arcname = f"{arc_root}/{name}"
            info = tf.gettarinfo(full, arcname=arcname)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if info.issym():
                info.mode = 0o777
                tf.addfile(info)
            else:
                # Normalizes to git-archive-like fixed modes (git-archive itself would emit
                # 100644/100755 from the index; nothing under ofw-mcp/ needs to be executable).
                info.mode = 0o644
                with open(full, "rb") as fh:
                    tf.addfile(info, fh)
PY
}

# want_ofw_mcp: true (0) if ofw-mcp should be packaged. Gate fix (item 8): OFW_MCP_SKIP=1 skips
# unconditionally -- sha file present or not -- and always prints the line, so it can never be
# silently ignored by a sha file that happens to exist. Refuses outright (exit 1) only when the
# sha file is missing AND OFW_MCP_SKIP is not set.
want_ofw_mcp() {
  if [ "$OFW_MCP_SKIP" = "1" ]; then
    log "OFW_MCP_SKIP=1; releasing without ofw-mcp/"
    return 1
  fi
  if [ -f "$OFW_MCP_SHA_FILE" ]; then
    return 0
  fi
  fail "missing $OFW_MCP_SHA_FILE; create it after the first ofw-mcp commit (see deps/README.md), or set OFW_MCP_SKIP=1 to release without ofw-mcp"
}

# package_ofw_mcp <tar_path>: checks out deps/ofw-mcp.sha from OFW_MCP_SRC into a scratch clone,
# builds the wheel, and appends wheel + requirements-lock.txt + pyproject.toml + tests/ under
# ofw-mcp/ in the tarball (AD40). Refuses an unknown sha, a checkout without requirements-lock.txt,
# and anything other than exactly one built wheel.
package_ofw_mcp() {
  local tar_path="$1" sha clone_dir dest_dir wheel_count
  sha="$(tr -d '[:space:]' <"$OFW_MCP_SHA_FILE")"
  [[ "$sha" =~ $OFW_SHA_RE ]] || fail "$OFW_MCP_SHA_FILE must contain exactly one 40-character git sha, got '$sha'"
  command -v uv >/dev/null 2>&1 || fail "uv not found on PATH (needed to build the ofw-mcp wheel)"
  [ -d "$OFW_MCP_SRC" ] || fail "OFW_MCP_SRC=$OFW_MCP_SRC does not exist"

  clone_dir="$(mktemp -d "$BUILD_DIR/ofw-mcp-src.XXXXXX")"
  CLONE_DIR="$clone_dir"  # item 12: the EXIT trap removes this if we fail anywhere below
  log "cloning $OFW_MCP_SRC at $sha"
  git clone --quiet -- "$OFW_MCP_SRC" "$clone_dir" || fail "could not clone OFW_MCP_SRC=$OFW_MCP_SRC"
  git -C "$clone_dir" checkout --quiet --detach "$sha" 2>/dev/null \
    || fail "unknown ofw-mcp sha $sha (not found in $OFW_MCP_SRC)"
  [ "$(git -C "$clone_dir" rev-parse HEAD)" = "$sha" ] || fail "ofw-mcp checkout did not land on $sha"
  [ -f "$clone_dir/requirements-lock.txt" ] \
    || fail "ofw-mcp checkout $sha has no requirements-lock.txt (the ofw-mcp repo must generate one with 'uv pip compile --generate-hashes', PLAN.md AD40)"
  [ -f "$clone_dir/pyproject.toml" ] || fail "ofw-mcp checkout $sha has no pyproject.toml"
  [ -d "$clone_dir/tests" ] || fail "ofw-mcp checkout $sha has no tests/ directory"

  dest_dir="$BUILD_DIR/ofw-mcp"
  install -d -m 0755 "$dest_dir/wheels"
  log "building the ofw-mcp wheel"
  (cd "$clone_dir" && uv build --wheel --no-create-gitignore --out-dir "$dest_dir/wheels") \
    || fail "uv build --wheel failed for ofw-mcp $sha"

  wheel_count="$(find "$dest_dir/wheels" -maxdepth 1 -name '*.whl' | wc -l | tr -d '[:space:]')"
  [ "$wheel_count" = "1" ] || fail "expected exactly one ofw-mcp wheel in $dest_dir/wheels, found $wheel_count"

  cp "$clone_dir/requirements-lock.txt" "$dest_dir/requirements-lock.txt"
  cp "$clone_dir/pyproject.toml" "$dest_dir/pyproject.toml"
  cp -R "$clone_dir/tests" "$dest_dir/tests"

  append_dir "$tar_path" "$dest_dir" "ofw-mcp"
  rm -rf -- "$clone_dir"
  CLONE_DIR=""
  log "packaged ofw-mcp $sha (1 wheel)"
}

assert_no_secrets() {
  # Basenames that must never appear anywhere in the tarball: any *.env (or *.env.<suffix>, e.g. a
  # stray .env.local), the API token file, and the credential/key/session-state shapes a stray
  # OAuth, service-account, or ofw-mcp local-profile setup might leave lying around (item 5:
  # secrets.json is ofw-mcp's own OFW_MCP_SECRETS_FILE name, state.json is its Playwright
  # storage_state -- neither must ever ship, even accidentally, under ofw-mcp/tests/).
  local tarball="$1" hits
  hits="$(tar -tzf "$tarball" \
    | grep -E '(^|/)([^/]*\.env|\.env(\.[^/]*)?|api_token|\.credentials\.json|credentials\.json|token\.json|client_secret[^/]*\.json|secrets[^/]*\.json|state\.json|[^/]*\.pem)$' \
    || true)"
  if [ -n "$hits" ]; then
    fail "release tarball would contain secret-shaped paths, refusing to upload:
$hits"
  fi
}

main() {
  require_clean_tree
  local sha tgz sha_file bucket tar_path
  # The release identity is the full 40-character git sha everywhere (S3 layout, tarball name,
  # releases/latest, VERSION, and jarvis-deploy.sh's own re-validation, which matches the SSM
  # document's allowedPattern ^[0-9a-f]{40}$ exactly).
  sha="$(git rev-parse HEAD)"
  tgz="jarvis-$sha.tar.gz"
  sha_file="$tgz.sha256"
  tar_path="${tgz%.gz}"

  install -d -m 0755 "$BUILD_DIR"
  build_tarball "$BUILD_DIR/$tar_path" "$sha"

  # AD40: ofw-mcp/ is appended to the still-uncompressed tarball before gzip, so a missing
  # deps/ofw-mcp.sha (without OFW_MCP_SKIP=1) fails the release before anything is uploaded.
  if want_ofw_mcp; then
    package_ofw_mcp "$BUILD_DIR/$tar_path"
  fi

  gzip -f "$BUILD_DIR/$tar_path"  # produces exactly $BUILD_DIR/$tgz
  assert_no_secrets "$BUILD_DIR/$tgz"
  (cd "$BUILD_DIR" && shasum -a 256 "$tgz" >"$sha_file")

  bucket="$(resolve_bucket)"
  log "uploading to s3://$bucket/releases/$sha/ (region $REGION)"
  aws s3 cp "$BUILD_DIR/$tgz" "s3://$bucket/releases/$sha/$tgz" --region "$REGION" --only-show-errors
  aws s3 cp "$BUILD_DIR/$sha_file" "s3://$bucket/releases/$sha/$sha_file" --region "$REGION" --only-show-errors
  printf '%s' "$sha" | aws s3 cp - "s3://$bucket/releases/latest" --region "$REGION" --only-show-errors

  log "released $sha ($tgz, $sha_file) -> s3://$bucket/releases/$sha/"
  printf '%s\n' "$sha"
}

# Only runs main when executed directly (`bash scripts/release.sh` / `make release`), not when
# sourced -- tests/test_ofw_packaging.py sources this file to exercise want_ofw_mcp/
# package_ofw_mcp in isolation, without needing build_tarball's `git archive HEAD` (which depends
# on this checkout's current commit, not on anything ofw-mcp specific) to succeed first.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
