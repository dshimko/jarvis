#!/usr/bin/env bash
# Best-effort first deploy at boot (infra/DESIGN.md section 6.4 step f). jarvis-deploy is owned
# by deploy-engineer (Phase 4) and may not exist yet on this instance; its absence, and a failed
# deploy, are both logged and bootstrap continues (PLAN.md "Bootstrap ordering" amendment).

run_first_deploy() {
  local bucket="$1" region="$2" sha

  # A 403 or a 404 both mean "no release yet" and are not errors (PLAN.md 3.2): both just leave
  # $sha empty, so no separate status-code handling is needed.
  sha=$(aws s3 cp "s3://$bucket/releases/DEPLOYED" - --region "$region" 2>/dev/null \
    | tr -d '[:space:]') || true

  if [ -z "$sha" ]; then
    log "no release yet (DEPLOYED marker missing), skipping first deploy"
    return 0
  fi

  if [ ! -x /opt/jarvis/bin/jarvis-deploy ]; then
    log "jarvis-deploy not present yet, skipping first deploy for sha=$sha"
    return 0
  fi

  if /opt/jarvis/bin/jarvis-deploy "$sha"; then
    log "first_deploy_ok sha=$sha"
    return 0
  fi
  log "first_deploy_failed sha=$sha"
  return 1
}
