#!/usr/bin/env bash
# /proc hardening (hidepid, PLAN.md AD32) and ssh removal (AD24). Unattended-upgrades (AD30)
# lives here too since it is the last small piece of host hardening bootstrap.sh applies.

# Gate 1 amendment: the fstab hidepid= value must be procadm's *numeric* gid. Mount-option
# parsing for /proc happens before name service switch lookups are reliably available in this
# context, so a bare group name can silently fail to apply.
setup_procadm_and_hidepid() {
  local procadm_gid
  procadm_gid=$(getent group procadm | cut -d: -f3)
  if [ -z "$procadm_gid" ]; then
    log "procadm group missing, cannot set hidepid"
    return 1
  fi

  local fstab_line="proc /proc proc defaults,hidepid=invisible,gid=$procadm_gid 0 0"
  if grep -q '^proc[[:space:]]\+/proc[[:space:]]\+proc[[:space:]]' /etc/fstab; then
    sed -i "s#^proc[[:space:]]\+/proc[[:space:]]\+proc[[:space:]].*#$fstab_line#" /etc/fstab
  else
    echo "$fstab_line" >> /etc/fstab
  fi
  mount -o remount /proc

  # systemd-logind (and polkit, if installed) read other users' /proc entries for session
  # management; without procadm as a supplementary group hidepid=invisible would break them.
  install -d -m 0755 /etc/systemd/system/systemd-logind.service.d
  install -m 0644 -o root -g root "$SCRIPT_DIR/systemd/logind-procadm.conf" \
    /etc/systemd/system/systemd-logind.service.d/procadm.conf

  if systemctl list-unit-files polkit.service >/dev/null 2>&1; then
    install -d -m 0755 /etc/systemd/system/polkit.service.d
    install -m 0644 -o root -g root "$SCRIPT_DIR/systemd/polkit-procadm.conf" \
      /etc/systemd/system/polkit.service.d/procadm.conf
  fi

  systemctl daemon-reload
  systemctl restart systemd-logind.service 2>/dev/null || true
}

mask_ssh_and_remove_instance_connect() {
  systemctl mask ssh.service ssh.socket 2>/dev/null || true
  systemctl stop ssh.service ssh.socket 2>/dev/null || true
  DEBIAN_FRONTEND=noninteractive apt-get purge -y ec2-instance-connect >/dev/null 2>&1 || true
}

configure_unattended_upgrades() {
  local reboot_time="$1"
  # ${distro_id} / ${distro_codename} below are apt-config variables, not shell: kept literal
  # with backslash escapes so `set -u` never trips over them.
  cat > /etc/apt/apt.conf.d/51jarvis-unattended-upgrades <<EOF
Unattended-Upgrade::Allowed-Origins {
        "\${distro_id}:\${distro_codename}-security";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "$reboot_time";
EOF
  {
    echo 'APT::Periodic::Update-Package-Lists "1";'
    echo 'APT::Periodic::Unattended-Upgrade "1";'
  } > /etc/apt/apt.conf.d/52jarvis-periodic
  systemctl enable --now unattended-upgrades.service 2>/dev/null || true
}
