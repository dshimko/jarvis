#!/usr/bin/env bash
# scripts/release.sh: builds a release tarball of the app + vault templates, verifies it excludes
# secrets, uploads it to the artifacts bucket, and writes releases/latest (informational only --
# not read by any deploy or bootstrap logic; `make deploy` writes the operational
# releases/DEPLOYED marker on success, which ops/aws/lib/first-deploy.sh reads at boot).
# `make release` wraps this. infra/PLAN.md deploy-engineer deliverable 1; infra/DESIGN.md 8.1.
#
# Region-deny SCP consequence (PLAN.md 3.2): every aws call passes --region $REGION explicitly
# (REGION defaults to us-east-1; AD34).
# Bucket key-pinning Deny (PLAN.md 3.2): the artifacts bucket's DenyWrongKmsKey policy rejects an
# upload naming the wrong SSE-KMS key AND one naming none-with-a-null-key-id, so the only
# compliant upload is one with NO server-side-encryption header at all; `aws s3 cp` below never
# passes an --sse flag.
#
# Runs on the workstation (this is the human's Mac), not on the box: uses `shasum -a 256`
# directly rather than jarvis-deploy.sh's sha256sum/shasum fallback, since macOS never has
# sha256sum on PATH.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
REGION="${AWS_REGION:-us-east-1}"
ARTIFACTS_BUCKET="${ARTIFACTS_BUCKET:-}"
BUILD_DIR="${JARVIS_RELEASE_BUILD_DIR:-$(mktemp -d)}"
# M4: requirements-lock.txt (uv pip compile --generate-hashes, includes requirements.txt via
# requirements-dev.txt's own -r line) ships alongside the two source files so jarvis-deploy.sh can
# `pip install --require-hashes` from it.
ARCHIVE_PATHS="jarvis requirements.txt requirements-dev.txt requirements-lock.txt pytest.ini tests mcp vaults ops/aws scripts"

log() { printf '[release] %s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

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
  # Archives uncompressed first so VERSION (generated, not tracked -- DESIGN.md 8.1) can be
  # appended before gzip; `git archive` alone can only emit what's in the committed tree.
  local out="$1" sha="$2" config_files tar_path
  config_files="$(git ls-files -- 'config*.yaml')"
  tar_path="${out%.gz}"
  # git archive builds straight from the committed tree at HEAD, so every path below -- vaults/
  # included -- is templates-only by construction: nothing untracked can ever be in it. It also
  # always normalizes entry ownership to uid/gid 0, which the appended VERSION member (below)
  # must match explicitly since it isn't a git-archive entry.
  # shellcheck disable=SC2086  # $config_files/$ARCHIVE_PATHS: intentional word-split filename lists
  git archive --format=tar --output="$tar_path" HEAD -- $config_files $ARCHIVE_PATHS \
    || fail "git archive failed"

  append_version "$tar_path" "$sha"
  gzip -f "$tar_path"  # produces exactly $out ($tar_path == ${out%.gz})
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

assert_no_secrets() {
  # Basenames that must never appear anywhere in the tarball: any *.env, the API token file, and
  # the credential/key shapes a stray OAuth or service-account setup might leave lying around.
  local tarball="$1" hits
  hits="$(tar -tzf "$tarball" \
    | grep -E '(^|/)([^/]*\.env|api_token|\.credentials\.json|credentials\.json|token\.json|client_secret[^/]*\.json|[^/]*\.pem)$' \
    || true)"
  if [ -n "$hits" ]; then
    fail "release tarball would contain secret-shaped paths, refusing to upload:
$hits"
  fi
}

main() {
  require_clean_tree
  local sha tgz sha_file bucket
  # The release identity is the full 40-character git sha everywhere (S3 layout, tarball name,
  # releases/latest, VERSION, and jarvis-deploy.sh's own re-validation, which matches the SSM
  # document's allowedPattern ^[0-9a-f]{40}$ exactly).
  sha="$(git rev-parse HEAD)"
  tgz="jarvis-$sha.tar.gz"
  sha_file="$tgz.sha256"

  install -d -m 0755 "$BUILD_DIR"
  build_tarball "$BUILD_DIR/$tgz" "$sha"
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

main "$@"
