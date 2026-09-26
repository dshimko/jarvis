#!/usr/bin/env bash
# ops/aws/bootstrap.sh: full instance bootstrap. Run once by user_data.sh (cloud-init) and safe
# to re-run manually (e.g. over SSM) for recovery -- every step below is idempotent.
#
# Order follows infra/DESIGN.md section 6.4 step 4 (a-g), tightened by the Gate 1 amendments:
# iptables before users, and the IMDS guard's uid/curl assert never runs before the mode users
# exist (bootstrap step a calls the guard with --rules-only; the full check only runs later, via
# jarvis-imds-guard.service, once step b has created the users).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/log.sh
source "$SCRIPT_DIR/lib/log.sh"
# shellcheck source=lib/users.sh
source "$SCRIPT_DIR/lib/users.sh"
# shellcheck source=lib/iptables.sh
source "$SCRIPT_DIR/lib/iptables.sh"
# shellcheck source=lib/harden.sh
source "$SCRIPT_DIR/lib/harden.sh"
# shellcheck source=lib/tailscale-join.sh
source "$SCRIPT_DIR/lib/tailscale-join.sh"
# shellcheck source=lib/install-units.sh
source "$SCRIPT_DIR/lib/install-units.sh"
# shellcheck source=lib/claude-install.sh
source "$SCRIPT_DIR/lib/claude-install.sh"
# shellcheck source=lib/first-deploy.sh
source "$SCRIPT_DIR/lib/first-deploy.sh"

: "${JARVIS_ARTIFACTS_BUCKET:?JARVIS_ARTIFACTS_BUCKET must be set}"
: "${JARVIS_REGION:?JARVIS_REGION must be set}"
: "${JARVIS_INSTANCE_TYPE:=unknown}"
: "${JARVIS_TAILSCALE_SECRET_ID:?JARVIS_TAILSCALE_SECRET_ID must be set}"
: "${JARVIS_AUTO_REBOOT_TIME:=09:30}"

# /etc/jarvis/instance.env: bucket name, region, and the (non-secret) secret names every other
# ops/aws tool needs, so nothing else on the box hardcodes them (DESIGN.md section 6.2).
write_instance_env() {
  install -d -m 0755 -o root -g root /etc/jarvis
  cat > /etc/jarvis/instance.env <<EOF
JARVIS_ARTIFACTS_BUCKET=$JARVIS_ARTIFACTS_BUCKET
JARVIS_REGION=$JARVIS_REGION
JARVIS_INSTANCE_TYPE=$JARVIS_INSTANCE_TYPE
JARVIS_SECRET_WORK=jarvis/work
JARVIS_SECRET_PERSONAL=jarvis/personal
JARVIS_SECRET_SHARED=jarvis/shared
JARVIS_SECRET_TAILSCALE=jarvis/tailscale
JARVIS_SECRET_TOKEN_WORK=jarvis/work/api-token
JARVIS_SECRET_TOKEN_PERSONAL=jarvis/personal/api-token
EOF
  chmod 0644 /etc/jarvis/instance.env
}

overall_failed=0

log "step 0: writing /etc/jarvis/instance.env"
write_instance_env

log "step a: iptables rules + rules-only IMDS guard (users do not exist yet)"
install_iptables_rules "$SCRIPT_DIR"
"$SCRIPT_DIR/bin/jarvis-imds-guard" --rules-only

log "step b: users, directories, /proc hardening, ssh removal"
setup_users
setup_procadm_and_hidepid
mask_ssh_and_remove_instance_connect

log "step c: tailscale join"
tailscale_join "$JARVIS_TAILSCALE_SECRET_ID" "$JARVIS_REGION"

log "step d: install units, CloudWatch agent config, Syncthing overrides"
install_units "$SCRIPT_DIR"

log "step d2: initialize each mode's vault git repo (H5; libexec tool now installed)"
for mode in work personal; do
  runuser -u "jarvis-$mode" -- /opt/jarvis/libexec/jarvis-vault-commit || \
    log "vault_commit_init_failed mode=$mode"
done

log "step e: per-user Claude Code install"
install_claude_code_for_all_users

log "step e2: unattended-upgrades"
configure_unattended_upgrades "$JARVIS_AUTO_REBOOT_TIME"

log "step f: first deploy (best-effort; tolerates jarvis-deploy's absence)"
if ! run_first_deploy "$JARVIS_ARTIFACTS_BUCKET" "$JARVIS_REGION"; then
  log "first_deploy_failed"
  overall_failed=1
fi

log "step g: enable and start units (this runs the full IMDS guard for real)"
if ! enable_and_start_units; then
  log "unit_enable_or_start_failed"
  overall_failed=1
fi

log "step g: post-boot-assert"
if ! /opt/jarvis/bin/post-boot-assert; then
  log "post_boot_assert_failed"
  overall_failed=1
fi

if [ "$overall_failed" -ne 0 ]; then
  log "bootstrap finished with failures -- see cloud-init output and /jarvis/cloud-init"
  exit 1
fi
log "bootstrap complete"
