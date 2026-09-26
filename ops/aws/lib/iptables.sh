#!/usr/bin/env bash
# Installs the static IMDS/API/GUI iptables rules (infra/DESIGN.md section 6.5) and loads them.
# The rule files are static (no per-account values), so this is a plain, idempotent copy.
#
# L3: `iptables-restore --noflush` (not `netfilter-persistent reload`) so tailscaled's own
# dynamically-inserted chains survive. tailscaled starts as soon as its package is installed, in
# user_data.sh, before this ever runs -- a full reload/flush here would wipe its rules out.

install_iptables_rules() {
  local script_dir="$1"
  install -d -m 0750 -o root -g root /etc/iptables
  install -m 0640 -o root -g root "$script_dir/iptables/rules.v4" /etc/iptables/rules.v4
  install -m 0640 -o root -g root "$script_dir/iptables/rules.v6" /etc/iptables/rules.v6
  iptables-restore --noflush < /etc/iptables/rules.v4
  ip6tables-restore --noflush < /etc/iptables/rules.v6
}
