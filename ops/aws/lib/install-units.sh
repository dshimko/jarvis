#!/usr/bin/env bash
# Installs everything under ops/aws/{systemd,bin,libexec,cloudwatch,logrotate} to its runtime
# location and reloads systemd (infra/DESIGN.md section 6.3; PLAN.md AD18: ops/aws/ is the single
# source of truth, and jarvis-deploy reinstalls it from the release it deploys). Every step is a
# plain file copy, so re-running this is always safe.

install_units() {
  local script_dir="$1"

  # G5: `find -maxdepth 1 -type f` instead of a bare glob, so a stray directory (e.g. a
  # __pycache__ left by a local syntax check) under bin/ or libexec/ can never reach `install`
  # as an unexpected non-file argument.
  find "$script_dir/bin" -maxdepth 1 -type f -print0 \
    | xargs -0 --no-run-if-empty install -m 0700 -o root -g root -t /opt/jarvis/bin/
  install -m 0700 -o root -g root "$script_dir/post-boot-assert.sh" /opt/jarvis/bin/post-boot-assert
  find "$script_dir/libexec" -maxdepth 1 -type f -print0 \
    | xargs -0 --no-run-if-empty install -m 0755 -o root -g root -t /opt/jarvis/libexec/

  install -m 0644 -o root -g root "$script_dir/systemd/jarvis@.service" /etc/systemd/system/
  install -m 0644 -o root -g root "$script_dir/systemd/jarvis-secrets.service" /etc/systemd/system/
  install -m 0644 -o root -g root "$script_dir/systemd/jarvis-imds-guard.service" /etc/systemd/system/
  install -m 0644 -o root -g root "$script_dir/systemd/jarvis-logexport@.service" /etc/systemd/system/
  install -m 0644 -o root -g root "$script_dir/systemd/jarvis-vault-commit@.service" /etc/systemd/system/
  install -m 0644 -o root -g root "$script_dir/systemd/jarvis-vault-commit@.timer" /etc/systemd/system/

  install -d -m 0755 /etc/systemd/system/syncthing@.service.d
  install -m 0644 -o root -g root "$script_dir/systemd/syncthing-override.conf" \
    /etc/systemd/system/syncthing@.service.d/override.conf

  install -d -m 0755 -o root -g root /opt/aws/amazon-cloudwatch-agent/etc
  install -m 0644 -o root -g root "$script_dir/cloudwatch/amazon-cloudwatch-agent.json" \
    /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

  install -m 0644 -o root -g root "$script_dir/logrotate/jarvis" /etc/logrotate.d/jarvis

  if [ -x /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl ]; then
    /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
      -a fetch-config -m ec2 -s \
      -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
  fi

  systemctl daemon-reload
}

# M7: enable every unit first, then start each individually so one failure doesn't stop the
# rest from being attempted. Returns non-zero if anything failed to enable or start; the caller
# (bootstrap.sh) folds that into its own overall_failed and still runs post-boot-assert either
# way. jarvis@.service's own ConditionPathExists makes a "start" before the first deploy a clean
# skip (exit 0), not a failure, so it is unconditional here like everything else.
enable_and_start_units() {
  local units=(
    jarvis-imds-guard.service
    jarvis-secrets.service
    jarvis-logexport@work.service
    jarvis-logexport@personal.service
    syncthing@jarvis-work.service
    syncthing@jarvis-personal.service
    jarvis-vault-commit@work.timer
    jarvis-vault-commit@personal.timer
    amazon-cloudwatch-agent.service
    jarvis@work.service
    jarvis@personal.service
  )
  local failed=0 unit

  # This is the first time jarvis-imds-guard.service runs the FULL check (uid assert + curl
  # exit 7 per mode user): by now step b has created the users and install_units has put the
  # real binary at /opt/jarvis/bin/jarvis-imds-guard.
  if ! systemctl enable "${units[@]}"; then
    log "systemctl enable reported an error for at least one unit"
    failed=1
  fi

  for unit in "${units[@]}"; do
    if ! systemctl start "$unit"; then
      log "failed to start $unit"
      failed=1
    fi
  done

  return "$failed"
}
