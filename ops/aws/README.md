# ops/aws

Everything an instance needs to bootstrap and run Jarvis on AWS. This directory is the single
source of truth for units, scripts, iptables rules, Syncthing templates, and the CloudWatch agent
config (PLAN.md AD18): it is archived by Terraform into `bootstrap/<sha256>/bootstrap.tar.gz`, and
`jarvis-deploy` (deploy-engineer, Phase 4) re-installs it from every release, so unit changes ship
with a release and never require a new instance.

See `infra/PLAN.md`, `infra/DESIGN.md` (spec of record for instance-side behavior), and
`infra/DESIGN-IAM.md` for the decisions behind this layout.

## File tree

```
ops/aws/
  bootstrap.sh                    orchestrator: run once by user_data.sh, safe to re-run
  post-boot-assert.sh             brief 9.2 checks; installed as /opt/jarvis/bin/post-boot-assert
  lib/                            sourced by bootstrap.sh, not installed on the box
    log.sh                        log() helper
    users.sh                      users, groups, directories
    iptables.sh                   installs rules.v4/v6, netfilter-persistent reload
    harden.sh                     hidepid, logind/polkit drop-ins, ssh removal, unattended-upgrades
    tailscale-join.sh             tmpfs auth-key handling and `tailscale up`
    install-units.sh              installs bin/, libexec/, systemd/, cloudwatch/, logrotate/
    claude-install.sh             per-user native Claude Code install
    first-deploy.sh               best-effort first `jarvis-deploy` call
  iptables/rules.v4, rules.v6     static IMDS/API/GUI-port rules (DESIGN.md section 6.5)
  systemd/                        unit files and drop-ins, installed verbatim
    jarvis@.service                the AWS mode daemon unit (User=jarvis-%i, JARVIS_DEPLOYMENT=aws);
                                    ConditionPathExists gates it cleanly until the first release
                                    lands. The WSL/local unit (ops/jarvis@.service) is app-engineer's
  cloudwatch/amazon-cloudwatch-agent.json
  logrotate/jarvis
  bin/                            root-only tools -> /opt/jarvis/bin, mode 0700 each
    jarvis-secrets                sync + publish-token (Python 3 stdlib + aws cli subprocess)
    jarvis-status                 service/heartbeat/outbox/disk/tailscale report, --assert
    jarvis-logfilter               journal -> jsonl filter (Python 3 stdlib)
    jarvis-imds-guard              iptables + per-user IMDS check, --rules-only mode
  libexec/                        tools a mode user executes -> /opt/jarvis/libexec, mode 0755
    jarvis-write-env               atomic O_EXCL|O_NOFOLLOW env writer
    jarvis-vault-commit            git auto-commit, never pushes
    jarvis-syncthing-render        per-mode config.xml render, ExecStartPre gate
```

`jarvis-deploy`, `jarvis-restart`, and the `ops/aws/ssm/*.sh` command-document scripts are
deploy-engineer's (Phase 4); this phase's scripts call `jarvis-deploy` only if it is already
present and tolerate its absence with a logged warning.

## Deviations from PLAN.md, reconciled with DESIGN.md (which wins on detail)

- **Bootstrap unpack path.** PLAN.md's bootstrap-engineer role text says user_data unpacks to
  `/opt/jarvis/ops`; DESIGN.md section 6.2 places it at `/opt/jarvis/bootstrap/<sha256>/`. Used
  the DESIGN.md path.
- **`jarvis-vault-commit` and `jarvis-syncthing-init`.** PLAN.md's role text lists
  `jarvis-vault-commit` as a `bin/` (root) tool and a script named `jarvis-syncthing-init`.
  PLAN.md AD17 (tightened, section 3.2) and DESIGN.md section 6.2/6.3 move mode-user-executed
  tools to `libexec/` and name the Syncthing render script `jarvis-syncthing-render`. Followed
  DESIGN.md: both scripts live in `libexec/`, and the Syncthing tool is `jarvis-syncthing-render`.
- **Syncthing unit.** Implemented as a drop-in (`systemd/syncthing-override.conf` ->
  `/etc/systemd/system/syncthing@.service.d/override.conf`) on the Debian/Ubuntu package's own
  `syncthing@.service` template (where `%i` is the *username*, matching DESIGN.md's diagram
  `syncthing@jarvis-work.service`), rather than a dedicated `jarvis-syncthing@.service`. This
  matches DESIGN.md's literal unit names in section 6.3 without re-implementing the package unit.
- **IMDS guard `--rules-only` flag.** Added per the Gate 1 re-verification amendment relayed
  mid-build: bootstrap step (a) installs the iptables rules and runs the guard with
  `--rules-only` (rule presence + root-can-reach-IMDS only) before the mode users exist; the full
  uid-assert-and-curl-exit-7 check runs only later, once `jarvis-imds-guard.service` starts for
  real in step (g). The uid assert never runs before the users exist.
- **fstab `hidepid=` gid.** Uses procadm's *numeric* gid (read back with
  `getent group procadm | cut -d: -f3`), not the group name, per the same amendment.
- **logind/polkit supplementary groups.** Added `systemd/logind-procadm.conf` and
  `systemd/polkit-procadm.conf` drop-ins (the latter installed only if `polkit.service` is a
  known unit) so hidepid does not break session management, per the same amendment.
- **`jarvis-logfilter` second layer.** Beyond forwarding only JSON-object lines, it also strips
  the AD11 content-carrying keys recursively and case-insensitively, per the same amendment.
- **`mktemp -d`, never `mktemp -u` + `install -d`.** Every temp path in this phase's scripts is
  created with `mktemp -d` (directories) or plain `mktemp` (files) directly, per the same
  amendment; deploy-engineer's on-box test HOME (DESIGN.md section 8.3) is out of this phase's
  scope but follows the same rule.
- **Extra `lib/` directory.** Not itemized in PLAN.md's deliverable list; added to keep
  `bootstrap.sh` and every individual file under 300 lines (user's coding-style rule) while
  keeping one entry point. Purely an internal split of deliverable #2; nothing in `lib/` is
  installed on the box.
- **post-boot-assert checks beyond PLAN.md's shorter list.** DESIGN.md section 6.5 adds: EIP
  association, `ps -e` cross-mode visibility, `/proc` hidepid mount options, GUI-port rejection
  for the other uid, and API-bind-to-Tailscale-IP (skipped, not failed, before the first deploy).
  Implemented DESIGN.md's fuller list.

## Re-running bootstrap.sh safely

cloud-init runs `bootstrap.sh` once, but every step is idempotent so it can be re-run by hand
(for example over an SSM shell, as root) for recovery:

- User/group/directory creation checks `id -u` / `getent group` / uses `install -d` first.
- iptables rules are static files copied into place, then `netfilter-persistent reload` --
  re-running never appends a duplicate rule.
- systemd units are plain file copies followed by `daemon-reload`; `enable --now` on an
  already-enabled, already-running unit is a no-op.
- `tailscale up` can be called again safely; the join step checks `tailscale status --json` first
  and skips if already `Running`.
- The Claude Code native installer and the CloudWatch agent `.deb` both tolerate reinstall.
- The first-deploy step only acts if `releases/DEPLOYED` exists and calls the idempotent
  `jarvis-deploy` (deploy-engineer).

Required environment variables (set by `user_data.sh.tftpl` before invoking `bootstrap.sh`):
`JARVIS_ARTIFACTS_BUCKET`, `JARVIS_REGION`, `JARVIS_TAILSCALE_SECRET_ID`. Optional, with
defaults: `JARVIS_INSTANCE_TYPE` (`unknown`), `JARVIS_AUTO_REBOOT_TIME` (`09:30`).

## Local verification (macOS: no systemd, no `systemd-analyze verify`)

```bash
# Every shell script
find ops/aws -type f \( -name '*.sh' -o -path '*/bin/*' -o -path '*/libexec/*' \) -print0 \
  | xargs -0 -I{} sh -c 'file "{}" | grep -q "shell script" && (shellcheck "{}"; bash -n "{}")'

# JSON
jq . ops/aws/cloudwatch/amazon-cloudwatch-agent.json >/dev/null

# Python tools
python3 -m py_compile ops/aws/bin/jarvis-secrets ops/aws/bin/jarvis-logfilter \
  ops/aws/libexec/jarvis-write-env ops/aws/libexec/jarvis-syncthing-render
```

### Manual systemd unit-key checklist (no `systemd-analyze` on macOS)

For every `.service`/`.timer`/`.conf` under `systemd/`, checked by hand against the
`systemd.unit(5)`, `systemd.service(5)`, and `systemd.exec(5)` man pages known at review time:

- [ ] `[Unit]`/`[Service]`/`[Install]`/`[Timer]` section names are exact and correctly ordered.
- [ ] Every directive is a real key for its section (no typos silently ignored by systemd).
- [ ] `Type=`, `RemainAfterExit=`, `ExecStart=` are consistent (oneshot units either have
      `RemainAfterExit=yes` or are meant to exit and be reused).
- [ ] `After=`/`Requires=`/`Wants=`/`Before=` form the dependency chain in DESIGN.md section 6.3
      with no cycles.
- [ ] Template units use `%i`/`%h` correctly for the intended substitution (username for the
      Syncthing override, mode name for `jarvis-logexport@`/`jarvis-vault-commit@`).
- [ ] Hardening directives (`ProtectSystem=strict`, `ProtectHome=tmpfs` + `BindPaths=`,
      `NoNewPrivileges=`, `ProtectProc=invisible`, etc.) are spelled exactly as the man pages
      define them and do not conflict (e.g. `ReadWritePaths=` is inside what `ProtectSystem=`
      would otherwise block).
- [ ] `ExecStart=` followed by a second `ExecStart=` correctly clears the package unit's default
      command in the Syncthing drop-in (`syncthing@.service.d/override.conf`).
- [ ] `jarvis@.service`: `ConditionPathExists=` (not `AssertPathExists=`) is used deliberately, so
      a missing `/opt/jarvis/current/.venv/bin/python` before the first deploy is a clean, logged
      "skipped" job result (exit 0), not a failed unit or a `Restart=` crash loop.
      `Requires=jarvis-secrets.service jarvis-imds-guard.service` plus matching `After=` follow
      the section 6.3 rule that every mode-user unit depends on the IMDS guard. Hardening list
      cross-checked word-for-word against DESIGN.md section 6.3: `NoNewPrivileges`,
      `ProtectSystem=strict`, `ProtectHome=tmpfs` + `BindPaths=`, `ReadWritePaths=`, `PrivateTmp`,
      `PrivateDevices`, `ProtectProc=invisible` + `ProcSubset=pid`, `ProtectKernelTunables`,
      `ProtectKernelModules`, `ProtectControlGroups`, `RestrictSUIDSGID`, `LockPersonality`,
      `RestrictRealtime`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`,
      `SystemCallArchitectures=native`, `CapabilityBoundingSet=` (empty), `LimitCORE=0`.

Checked and passing for every unit added in this phase as of this writing.
