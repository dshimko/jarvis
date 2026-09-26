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
  iptables/rules.v4, rules.v6     static IMDS/API/GUI-port rules (DESIGN.md section 6.5); rules.v4
                                    also gates 8783 (ofw-mcp) to root + jarvis-personal (AD33)
  systemd/                        unit files and drop-ins, installed verbatim
    jarvis@.service                the AWS mode daemon unit (User=jarvis-%i, JARVIS_DEPLOYMENT=aws);
                                    ConditionPathExists gates it cleanly until the first release
                                    lands. The WSL/local unit (ops/jarvis@.service) is app-engineer's
    ofw-mcp.service                the OFW MCP server unit (User=jarvis-ofw, loopback 127.0.0.1:8783);
                                    ConditionPathExists gates it until a release ships ofw-venv (AD33/AD40)
    jarvis-logexport@.service      exports ${JARVIS_LOGEXPORT_UNIT} (default jarvis@%i.service);
                                    jarvis-logexport@ofw.service.d/unit.conf overrides that variable
                                    to ofw-mcp.service, since ofw-mcp is not a jarvis@<mode> unit (AD37)
  cloudwatch/amazon-cloudwatch-agent.json   ships work/personal/ofw jsonl and cloud-init output
  logrotate/jarvis                 */*.jsonl glob already covers ofw.jsonl, no separate stanza needed
  bin/                            root-only tools -> /opt/jarvis/bin, mode 0700 each
    jarvis-secrets                sync (work, personal, shared, and ofw targets; AD34) +
                                    publish-token (Python 3 stdlib + aws cli subprocess)
    jarvis-status                 service/heartbeat/outbox/disk/tailscale report, ofw-mcp
                                    unit state + /healthz ok/breaker, --assert
    jarvis-logfilter               journal -> jsonl filter (Python 3 stdlib); mode arg is a free
                                    label with no allow-list, so "ofw" already passes through it
    jarvis-imds-guard              iptables + per-user IMDS check (work, personal, ofw), --rules-only mode
  libexec/                        tools a mode user executes -> /opt/jarvis/libexec, mode 0755
    jarvis-write-env               atomic O_EXCL|O_NOFOLLOW env writer (used for jarvis-ofw's env too)
    jarvis-vault-commit            git auto-commit, never pushes
    jarvis-syncthing-render        per-mode config.xml render, ExecStartPre gate
```

### OFW MCP server additions (PLAN.md AD33-AD41, phase I / bootstrap half)

- **`jarvis-ofw` (uid/gid 2003).** A third OS user, not a mode: no vault, no `.claude`, no
  Syncthing state, no `jarvis@` unit. `ops/aws/lib/users.sh` creates it with the same
  `create_mode_user` helper as the mode users and, via `runuser` (never root, AD31), only
  `.jarvis`, `.local/state/ofw-mcp`, and `.cache/ms-playwright` under its home.
- **`ofw-mcp.service`.** Loopback-only (`127.0.0.1:8783`), `ConditionPathExists` on
  `ofw-venv/bin/ofw-mcp` so it stays a clean skip until deploy-engineer's `jarvis-deploy` ships
  that venv. Starts from the same hardening block as `jarvis@.service`; a comment in the unit
  lists the Chromium-related directives phase D may need to relax and in what order to check them
  (none are relaxed yet).
- **`rules.v4` 8783 and 9222 owner rules.** Same three-line shape as 8384/8385: 8783 (the MCP
  port) is ACCEPT uid 0, ACCEPT uid 2002 (`jarvis-personal`), REJECT tcp-reset for everyone else
  -- so `jarvis-work` and `jarvis-ofw` itself cannot connect, only root and personal. 9222 (the
  Chromium DevTools port `ofw-mcp login` opens for the SSM port-forward flow) is ACCEPT uid 0,
  ACCEPT uid 2003 (`jarvis-ofw`), REJECT tcp-reset for everyone else -- a gate I finding: without
  this rule any local uid could drive the logged-in browser over CDP. `rules.v6` is unchanged:
  it has no 8781/8782-shaped rules to mirror in the first place.
- **`jarvis-secrets sync` third target, `ofw`.** Fetches `jarvis/ofw` (no `jarvis/shared`
  merge). A `jarvis/ofw` with no version yet is *skipped* (`secrets_unpopulated`, not fatal): the
  two mode writes proceed regardless, since the mode daemons must not depend on the optional
  third secret (`ofw-mcp.service` refuses to start without its own env file either way). When
  ofw *is* populated: `shared_violations` between work and personal stays as today (shared keys
  exempted), and ofw is compared against each mode's *merged* env (shared included) with no
  exemption, plus a shadowed-key check (any `jarvis/ofw` key literally named the same as a
  `jarvis/shared` key is a violation regardless of value) -- a violation anywhere blocks all
  three writes, not just the pair it was found in. Writes `/home/jarvis-ofw/.jarvis/env` through
  the same `runuser -u jarvis-ofw -- jarvis-write-env` shim as the modes. `publish-token` is
  unchanged: ofw has no API token to publish.
- **`jarvis-status`.** Lists the `ofw-mcp.service` and `jarvis-logexport@ofw.service` unit
  states, and prints `/healthz`'s `ok`/`breaker` fields only (never the raw body) from
  `curl 127.0.0.1:8783/healthz`, run as root (one of the two uids the 8783 rule allows through).
  `ok` prints only when `(.ok|type)=="boolean"` (a bare `has("ok")` would still accept a
  non-boolean value, and jq's `//` treats a JSON `false` as falsy too, so `.ok // empty` would
  misreport a real `false` as "unknown"); `breaker` is restricted to the `open`/`closed` enum,
  anything else prints "unknown".
- **`post-boot-assert.sh` additions.** iptables `-C` for all three lines of both the 8783 and
  9222 owner rules; a dedicated 8783 bind-address check that fails on *any* non-127.0.0.1
  listener (a Tailscale or VPC address, not only `0.0.0.0`/`*`/`[::]`; guarded on non-empty `ss`
  output first, since `echo "" | grep -v` matches the single empty line `echo` still emits and
  would otherwise print a false failure on top of the correct SKIP-FAIL below); a live check
  that, only once a `127.0.0.1:8783` listener is confirmed (a closed port and a REJECT tcp-reset
  both give `curl` exit 7, so testing `jarvis-work`'s rejection before that would be a false
  pass), curls `/healthz` as `jarvis-personal` (expect exit 0) and as `jarvis-work` (expect exit
  7); when nothing listens at all, an explicit `FAIL: SKIP-FAIL: ...` line, never a silent skip
  counted as PASS. `jarvis-ofw` is in the IMDS-unreachable loop and the cross-home loop now
  covers all six ordered pairs of the three users (previously only four); the process-visibility
  check adds `jarvis-personal` -> cannot see `jarvis-ofw`, alongside the existing `jarvis-ofw` ->
  cannot see `jarvis-personal`; `/home/jarvis-ofw` is 0700 and owned by `jarvis-ofw`.
- **`ofw-mcp.service` AD40 amendment: a second start condition.** `ConditionPathExistsGlob=
  /home/jarvis-ofw/.jarvis/env` alongside the existing `ConditionPathExists=` on the binary, so
  the unit is skipped (not flapping) until the human populates `jarvis/ofw`. Deliberately *not* a
  second `ConditionPathExists=` (which is what PLAN.md's prose literally says): systemd combines
  repeated instances of the *same* condition directive with OR and different directive types with
  AND, so two `ConditionPathExists=` lines would start the unit once *either* path existed --
  `jarvis-secrets` writes the env file on every boot regardless of release state, so it can exist
  well before `ofw-venv` does, and that OR reading would defeat the whole point of the amendment
  (the unit would attempt to start with no binary present and crash-loop, the exact flapping this
  is meant to prevent). `ConditionPathExistsGlob=` with no glob metacharacters is a plain
  existence check, functionally identical to `ConditionPathExists=` for a literal path, but a
  distinct directive type, so it is properly AND-combined with the first condition.
- **`jarvis-logfilter`.** Mode validation checked, per the brief: there isn't any -- the mode
  argument is a free label used only in the `nonjson_dropped` summary line, so `ofw` already
  passes through unchanged. `DROP_KEYS` (the second content-protection layer) gained the AD37
  ofw keys: `title, description, sender, recipients, thread, messages, events, expenses,
  entries, attachments`.

`jarvis-deploy`, `jarvis-restart`, and the `ops/aws/ssm/*.sh` command-document scripts are
deploy-engineer's (Phase 4); this phase's scripts call `jarvis-deploy` only if it is already
present and tolerate its absence with a logged warning.

## Deviations from PLAN.md, reconciled with DESIGN.md (which wins on detail)

- **AD34 "hash-dir entry like the modes".** Read as: `jarvis-ofw`'s env is written through the
  exact same mechanism as the modes' (`write_env()` -> `runuser -u jarvis-ofw --
  jarvis-write-env`, atomic, 0600), not as a request for a new on-disk hash file -- `sync` does
  not maintain one for work or personal either (only `publish-token`'s `HASH_DIR` does, and that
  is unchanged: ofw has no token). No new hash-tracking file was added.
- **Pre-existing bug fixed in passing: `APP_ROOT` was a plain `str`.** Writing the first
  end-to-end test for `cmd_sync()` (this phase's tests were the first to exercise it, rather
  than only its helper functions in isolation) found that `jarvis.secrets_check.
  mcp_literal_violations` does `root / "mcp" / name`, which raises `TypeError` on a plain
  string; `shared_violations_source()`/`mcp_literal_violations_source()` prefer that real
  function whenever `jarvis.secrets_check` imports (true in this repo, and true on a real
  instance once a release exists), so `cmd_sync()` would have crashed on every sync, for work
  and personal too, not just ofw -- this was never a `-1` exit, it was an unhandled exception.
  Fixed by making `APP_ROOT` a `Path`. Not otherwise in scope for this task, but `jarvis-secrets`
  is this phase's file and the fix is one line plus an import.
- **File length.** `ops/aws/bin/jarvis-secrets` (322 lines) and `ops/aws/post-boot-assert.sh`
  (349 lines) are over the earlier 300-line guidance after the AD33/AD34 additions and the gate
  I fix-required round (9222 rule, the amended ofw-vs-merged-env comparison, the 8783 bind and
  liveness checks); both were already at 287/259 lines before this phase. Left as single files
  rather than split, since both stay single-purpose and well under the user's global 800-line
  ceiling; flagged here rather than silently exceeded.
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
- [ ] `ofw-mcp.service`: same hardening/gating checklist as `jarvis@.service`, home swapped for
      `jarvis-ofw`'s; `StartLimitIntervalSec=0` is under `[Unit]`, not `[Service]`. No
      `ExecStartPost` (unlike `jarvis@.service`, ofw has no API token to publish).
- [ ] `jarvis-logexport@.service` / `jarvis-logexport@ofw.service.d/unit.conf`: the template's
      `Environment=JARVIS_LOGEXPORT_UNIT=jarvis@%i.service` and the drop-in's
      `Environment=JARVIS_LOGEXPORT_UNIT=ofw-mcp.service` are the same key -- systemd applies
      drop-ins after the main unit file, so the later value (the drop-in's) wins; this is an
      override, not an additive list, unlike `Before=`/`After=`/`Wants=`/`Requires=`.

Checked and passing for every unit added in this phase as of this writing.
