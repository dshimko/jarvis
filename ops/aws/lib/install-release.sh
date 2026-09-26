#!/usr/bin/env bash
# ops/aws/lib/install-release.sh: installs a release's ops/aws/ tree onto this box (root tools,
# libexec tools, systemd units, iptables rules, the CloudWatch agent config, logrotate config),
# then reloads the affected services. Added by deploy-engineer (Phase 4) so
# ops/aws/ssm/jarvis-deploy.sh does not duplicate ops/aws/lib/install-units.sh and lib/users.sh;
# sourced directly from the release being installed (ops/aws/ is shipped with every release,
# PLAN.md AD18), never from a fixed path, so this file's own future changes ship with a release
# too.
#
# NOTE FOR THE BOOTSTRAP OWNER: ops/aws/lib/first-deploy.sh currently calls
# /opt/jarvis/bin/jarvis-deploy directly rather than sourcing this; once jarvis-deploy.sh installs
# itself to that path (which it now does, see ops/aws/ssm/jarvis-deploy.sh), first-deploy.sh's
# existing behavior is unaffected and needs no change on your side. This file is offered in case a
# future bootstrap step wants the same install logic directly; not wired into bootstrap.sh here,
# since this phase does not edit existing ops/aws/lib files.
#
# install_release_files <src_ops_aws_dir> <bin_dir> <libexec_dir> <systemd_dir> <iptables_dir> \
#   <cw_config_dir> <logrotate_dir>
# Every step returns 1 immediately on the first failure. Deliberately never relies on `set -e`:
# callers often invoke this as part of an `if`/`&&`/`||` condition, where -e is suspended for the
# whole call (see jarvis-deploy.sh's own comment on this), so control flow here is explicit.
install_release_files() {
  local src="$1" bin_dir="$2" libexec_dir="$3" systemd_dir="$4" iptables_dir="$5" \
        cw_config_dir="$6" logrotate_dir="$7" f

  [ -d "$src" ] || { echo "[install-release] no ops/aws directory at $src" >&2; return 1; }

  install -d -m 0755 "$bin_dir" "$libexec_dir" || return 1
  for f in "$src"/bin/*; do
    [ -e "$f" ] || continue
    install -m 0700 "$f" "$bin_dir/$(basename "$f")" || return 1
  done
  if [ -f "$src/post-boot-assert.sh" ]; then
    install -m 0700 "$src/post-boot-assert.sh" "$bin_dir/post-boot-assert" || return 1
  fi
  if [ -f "$src/ssm/jarvis-deploy.sh" ]; then
    # See jarvis-deploy.sh's own comment: this closes the gap where nothing else ever installs a
    # persistent /opt/jarvis/bin/jarvis-deploy that ops/aws/lib/first-deploy.sh looks for.
    install -m 0700 "$src/ssm/jarvis-deploy.sh" "$bin_dir/jarvis-deploy" || return 1
  fi
  for f in "$src"/libexec/*; do
    [ -e "$f" ] || continue
    install -m 0755 "$f" "$libexec_dir/$(basename "$f")" || return 1
  done

  install -d -m 0755 "$systemd_dir" || return 1
  for f in "$src"/systemd/*.service "$src"/systemd/*.timer; do
    [ -e "$f" ] || continue
    install -m 0644 "$f" "$systemd_dir/$(basename "$f")" || return 1
  done
  if [ -f "$src/systemd/syncthing-override.conf" ]; then
    install -d -m 0755 "$systemd_dir/syncthing@.service.d" || return 1
    install -m 0644 "$src/systemd/syncthing-override.conf" \
      "$systemd_dir/syncthing@.service.d/override.conf" || return 1
  fi
  if [ -f "$src/systemd/logind-procadm.conf" ]; then
    install -d -m 0755 "$systemd_dir/systemd-logind.service.d" || return 1
    install -m 0644 "$src/systemd/logind-procadm.conf" \
      "$systemd_dir/systemd-logind.service.d/procadm.conf" || return 1
  fi
  if [ -f "$src/systemd/polkit-procadm.conf" ] && [ -e "$systemd_dir/polkit.service" ]; then
    install -d -m 0755 "$systemd_dir/polkit.service.d" || return 1
    install -m 0644 "$src/systemd/polkit-procadm.conf" "$systemd_dir/polkit.service.d/procadm.conf" \
      || return 1
  fi

  if [ -f "$src/iptables/rules.v4" ] || [ -f "$src/iptables/rules.v6" ]; then
    install -d -m 0755 "$iptables_dir" || return 1
  fi
  [ -f "$src/iptables/rules.v4" ] && { install -m 0640 "$src/iptables/rules.v4" "$iptables_dir/rules.v4" || return 1; }
  [ -f "$src/iptables/rules.v6" ] && { install -m 0640 "$src/iptables/rules.v6" "$iptables_dir/rules.v6" || return 1; }
  if { [ -f "$iptables_dir/rules.v4" ] || [ -f "$iptables_dir/rules.v6" ]; } \
     && command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent reload || return 1
  fi

  if [ -f "$src/cloudwatch/amazon-cloudwatch-agent.json" ]; then
    install -d -m 0755 "$cw_config_dir" || return 1
    install -m 0644 "$src/cloudwatch/amazon-cloudwatch-agent.json" \
      "$cw_config_dir/amazon-cloudwatch-agent.json" || return 1
  fi
  if [ -f "$src/logrotate/jarvis" ]; then
    install -d -m 0755 "$logrotate_dir" || return 1
    install -m 0644 "$src/logrotate/jarvis" "$logrotate_dir/jarvis" || return 1
  fi

  systemctl daemon-reload || return 1
}
