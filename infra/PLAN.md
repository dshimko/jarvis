# AWS migration: orchestration plan

Planner: Fable (planning and gating only; writes no code). Builders and reviewers: Opus or Sonnet
subagents defined in `.claude/agents/`. Every subagent reads this file, `infra/BRIEF.md` (the
original brief), `README.md`, and `PORTING.md` before starting. This file is the single source of
truth for interfaces that more than one builder depends on. If a builder needs to change an
interface listed here, it stops and reports instead of improvising.

Status legend: `[ ]` open, `[x]` done, `[!]` blocked on the human.

## 1. What exists today (repo audit, 2026-09-25)

- One daemon (`jarvis/main.py`) loads both modes (`modes.load_modes()`), binds `127.0.0.1:8765`,
  starts Slack (work) and Telegram (personal), APScheduler jobs, and an outbox poller. Isolation is
  per `claude -p` subprocess (env, cwd, `--mcp-config`, deny lists), not per OS user.
- Vault paths come from `config.yaml` via `${WIN_HOME}` (WSL interop). `jarvis/paths.py` is WSL-
  specific (`wslpath`, `cmd.exe`, `%LOCALAPPDATA%`). `api.copy_token_to_windows` writes the token to
  the Windows profile.
- Logging is plain `logging.basicConfig` text; nothing structured; no heartbeat; no metrics.
- There is **no OFW watcher** and no `jarvis/gmail_watch.py`. OFW is checked by a scheduled
  `/ofw-check` vault command at 12:00 (personal) through the OFW MCP. The brief treats the watcher
  as existing; it is net-new work (see G2).
- The Windows client (`windows_client/`) holds one `api_url`, one token file, one SSE stream.
- Tests: 12 files under `tests/`, 15 under `windows_client/tests/`. They need Python 3.12 and a
  venv (system Python is 3.9 and `mcp` is not installed there).
- Tooling on this Mac after Phase 0: Terraform 1.16.4 through `tfenv` (the Homebrew 1.5.7
  formula stays installed but unlinked), tflint 0.64.0 at `~/.local/bin/tflint` (official GitHub
  release binary; Homebrew has no formula any more, so Makefile targets must not assume a brew
  path), trivy 0.74, shellcheck 0.11, `aws` CLI 2.34, `uv` 0.10. `.venv` is created with
  `uv venv .venv --python 3.12 --seed` (the bare `python3.12 -m venv` fails on the uv-managed
  interpreter). Baseline: 300 passed, 11 skipped, 88% line coverage on `jarvis/`.
  `session-manager-plugin` is not installed: the cask needs an interactive sudo password.
- AWS profiles (verified read-only on 2026-09-25): `sparko` is IAM user `Administrator` in account
  `080109295043`, which is the management account of Organization `o-lcwqey40mr`; it is the
  profile for `infra/org/`. The `default` profile has an expired SSO grant. No `jarvis-prod`
  profile exists yet. The runbook should recommend moving the management-account work from the
  long-lived IAM user to an Identity Center role; that is the human's existing setup, not
  something this migration creates.
- Git: `origin git@github.com:dshimko/jarvis.git`, branch `main`, initialised during this
  planning session (it was not a repo when the audit started). Commits happen only when the human
  asks; work lands on a feature branch, never directly on `main`.

## 2. Gaps between the brief and the repo, with resolutions

| # | Gap | Resolution |
|---|---|---|
| G1 | Was not a git repo at audit time; `make release` needs a sha | Resolved 2026-09-25: the human initialised the repo (`origin git@github.com:dshimko/jarvis.git`, branch `main`, one commit). `make release` still refuses outside a clean git checkout. `[x]` |
| G2 | OFW watcher does not exist | Build it (AD10). Scope is deliberately minimal and read-only. |
| G3 | IMDS is root-only, but the brief has each daemon call `PutMetricData` | Daemons never hold AWS credentials. Heartbeat and error metrics come from CloudWatch Logs metric filters on the shipped JSON logs (AD1). |
| G4 | The instance-role IAM list omits the `PutSecretValue` the root secrets service needs to publish API tokens (brief 4.3) | Add `secretsmanager:PutSecretValue` scoped to exactly `jarvis/work/api-token` and `jarvis/personal/api-token`, plus `kms:GenerateDataKey` on the jarvis key. Documented deviation in DESIGN.md (AD6). |
| G5 | The CloudWatch agent cannot read journald | Root `jarvis-logexport@<mode>.service` streams the unit's journal to `/var/log/jarvis/<mode>.jsonl`; the agent tails that (AD2). |
| G6 | Two Syncthing instances cannot both listen on 22000 on one address | work 22000, personal 22001 (TCP+UDP), both bound to the Tailscale IP only (AD8). |
| G7 | Terraform 1.5.7 locally | Phase 0 installs Terraform >= 1.10, tflint, trivy. |
| G8 | `plan.out` for `envs/prod` needs the `jarvis-prod` account and a profile that does not exist yet | Phase 2 ships validate, tflint, trivy, and `terraform test` with mock providers. `make plan` produces the real `plan.out` once the human has the account and `AWS_PROFILE=jarvis-prod` (AD12). `[!]` |
| G9 | Local mode is one daemon on 8765; the brief wants one daemon per mode | Per-mode daemons everywhere, including WSL: `jarvis@work` on 127.0.0.1:8781, `jarvis@personal` on 127.0.0.1:8782 (AD4, AD13). One code path, no combined mode. |
| G10 | Claude Code MCP OAuth callback port is not documented in the brief | deploy-engineer verifies it against current Claude Code docs (Context7) and makes the forwarded port list configurable in `scripts/oauth-login.sh`. |
| G11 | Tests cannot run on system Python | Phase 0 creates `.venv` with Python 3.12. |
| G12 | Brief 1 says each user has its own API port and 4.2 says the bind address comes from `tailscale ip -4`; `TrustedHostMiddleware` today allows only loopback | aws profile allows the Tailscale IP, `jarvis`, and `*.ts.net` (AD5). |

## 3. Architecture decisions (fixed; builders do not re-open these)

- **AD1 Metrics via log metric filters.** Each daemon logs `{"event":"heartbeat","mode":"<mode>"}`
  every 300 s. Terraform creates metric filters on `/jarvis/<mode>`: `Jarvis/Heartbeat` (dimension
  `Mode`, value 1) and `Jarvis/OfwWatchErrors` (from `event=ofw_watch_error`). Heartbeat alarm:
  period 300 s, 3 evaluation periods, `SampleCount < 1`, `treat_missing_data = breaching`.
  OfwWatchErrors alarm: `Sum > 0` for 3 periods. The CloudWatch agent publishes disk metrics under
  namespace `Jarvis` so the role's `PutMetricData` condition (`cloudwatch:namespace = Jarvis`) holds.
  The daemons make zero AWS API calls.
- **AD2 Log pipeline.** App logs one JSON object per line to stdout (journald). Root unit
  `jarvis-logexport@<mode>.service` runs `journalctl -f -o cat -u jarvis@<mode>` into
  `/var/log/jarvis/<mode>.jsonl` (0640 root:adm, logrotate daily, keep 7). CloudWatch agent ships
  `/var/log/jarvis/work.jsonl` to `/jarvis/work`, `personal.jsonl` to `/jarvis/personal`, and
  `/var/log/cloud-init-output.log` to `/jarvis/cloud-init`. The content filter (AD11) runs inside the
  app before any handler, so content never reaches journald either.
- **AD3 Deployment profile resolution.** `JARVIS_DEPLOYMENT` env var wins (systemd on AWS sets
  `aws`), else `deployment:` in `config.yaml`, else `local`. `config.<profile>.yaml` is deep-merged
  over `config.yaml`. A missing profile file is a startup error. The release tarball ships all three.
- **AD4 One daemon per mode, everywhere.** `python -m jarvis.main --mode work|personal`.
  `modes.load_mode(name, root, cfg, profile)` builds exactly one `Mode`; it never opens the other
  mode's env file. `peers` (the deny list for the other vault) is derived from the profile's vault
  path template for the other mode name, no env access. `load_modes()` is removed; tests that used it
  move to `load_mode`. The redactor covers the daemon's own secrets (the other mode's secrets are
  never in the process). The cross-mode shared-secret check becomes a pure function
  `jarvis.secrets_check.shared_violations(work, personal, shared_keys)`; callers are the root
  `jarvis-secrets` tool on AWS and `install_wsl.sh` locally. Slack starts only in work, Telegram and
  the OFW watcher only in personal; schedules are filtered to the daemon's mode.
- **AD5 API bind.** Profile key `api.bind: loopback | tailscale`. `loopback` binds 127.0.0.1
  (local). `tailscale` runs `tailscale ip -4` at startup; empty output, non-zero exit, or a non-
  private address is a startup error (`SystemExit`), never a fallback. Allowed hosts: loopback
  profile `localhost, 127.0.0.1`; tailscale profile the resolved IP, `jarvis`, `*.ts.net`. Ports:
  work 8781, personal 8782 in both profiles. Exactly one bind address per daemon: the aws profile
  does not also listen on loopback. The deploy health check on the box resolves the address the same
  way (`tailscale ip -4`).
- **AD6 API tokens.** `api.load_or_create_token()` unchanged (`~/.jarvis/api_token`, 0600, O_EXCL).
  `copy_token_to_windows` runs only under the local profile. On AWS, `jarvis@.service` has
  `ExecStartPost=+/opt/jarvis/bin/jarvis-secrets publish-token %i` (the `+` runs it as root), which
  reads `/home/jarvis-%i/.jarvis/api_token` and calls `PutSecretValue` on `jarvis/%i/api-token` only
  when the value changed. Six secrets total (brief's four plus the two token secrets), all created
  empty by Terraform.
- **AD7 Secret format.** `jarvis/work`, `jarvis/personal`, `jarvis/shared` hold a JSON object of
  `KEY: value`. `jarvis-secrets sync` merges `shared` into each mode, runs `shared_violations`
  (exempting keys present in `jarvis/shared`), and on any violation writes nothing and exits 1
  (unit fails, `jarvis@*` will not start because of `Requires=jarvis-secrets.service`). Output file
  `/home/jarvis-<mode>/.jarvis/env`, `KEY=value` lines, 0600, owned by that user, written atomically.
  `jarvis/tailscale` is `{"authkey": "..."}`; cloud-init reads it into a tmpfs file
  (`/run/jarvis/ts-authkey`, 0600), runs `tailscale up --auth-key=file:...`, then shreds the file.
  Recommend a one-off, pre-authorised, tagged key; the runbook says so.
- **AD8 Syncthing.** One instance per user, `syncthing@jarvis-<mode>.service` (system unit with
  `User=`), config under `/home/jarvis-<mode>/.local/state/syncthing`. Listen `tcp://<ts-ip>:22000`
  and `quic://<ts-ip>:22000` for work, 22001 for personal. GUI `127.0.0.1:8384` (work) and
  `127.0.0.1:8385` (personal). `relaysEnabled=false`, `globalAnnounceEnabled=false`,
  `localAnnounceEnabled=false`, `natEnabled=false`, `urAccepted=-1`. One folder per instance: the
  user's vault, folder id `jarvis-<mode>`. `.stignore`: `.obsidian/workspace*.json`, `.trash/`,
  `outbox/.lock`, and `.git/` (stricter than the brief: git history stays on the server and is never
  synced to the workstation). Devices are added by the runbook, not by bootstrap.
- **AD9 Conflict gate.** `outbox.check_gates` gains a first gate: if any file matching
  `outbox/<id>.sync-conflict-*` exists (or `outbox/<id>*.sync-conflict-*`), raise
  `Blocked("sync conflict: resolve <filename> first")`. Conflict files can never be approved by id
  because `ID_RE` rejects dots. Test: `tests/test_outbox_conflict.py`.
- **AD10 OFW watcher (net-new, personal daemon only).** `jarvis/gmail_watch.py`:
  - `python -m jarvis.gmail_watch --auth`: Google OAuth installed-app flow with scope
    `https://www.googleapis.com/auth/gmail.readonly` only, loopback callback on port 8766, client
    secrets JSON path from env `GMAIL_WATCH_CLIENT_SECRETS` (in the personal secret as a value; the
    watcher writes it to a 0600 temp file for the flow only). Token stored at
    `~/.jarvis/gmail_watch_token.json` (0600). Prints the connected account's email address.
  - Runtime: a daemon thread in the personal daemon, poll interval `ofw_watch.poll_minutes`
    (default 5), query `ofw_watch.query` (default `from:@ourfamilywizard.com is:unread newer_than:2d`),
    dedup by message id in `~/.jarvis/ofw_watch_seen.json`. It reads message ids and internalDate
    only; it never fetches bodies, never logs subjects. On new ids it calls
    `brain.ask_detailed(mode, "/ofw-check")` (existing vault command, coparent drafts into
    `outbox/`). It never modifies Gmail (no mark-read, no labels): dedup is local.
  - Telegram push: the personal daemon subscribes to `pending_new` on the event bus and sends
    "New draft `<id>`: `<first_line>`. Send `read <id>` to review." to the owner chat. The draft body
    travels only through the existing `read <id>` command.
  - Errors log `event=ofw_watch_error` with `error_class` only. Missing token file: log
    `ofw_watch_disabled` once and do not poll (no crash loop).
  - Dependencies: `google-api-python-client`, `google-auth`, `google-auth-oauthlib` (arm64 pure
    Python). Tests mock the Gmail service.
- **AD11 Structured logging and content filter.** `jarvis/logsetup.py`: JSON lines with keys
  `ts, level, mode, logger, event, msg` plus optional `id, duration_ms, status, error_class,
  job, channel`. A `logging.Filter` deletes, recursively and case-insensitively, any key named
  `body, text, draft, preview, snippet, subject, transcript` and, stricter than the brief,
  `readback, reply, prompt, first_line, args, utterance, message_text`. Applied to every handler.
  Text-format console output stays available for local dev (`JARVIS_LOG_FORMAT=text`).
  Tests: `tests/test_logfilter.py`.
- **AD12 Terraform plan reality.** Phase 2 cannot produce a real `plan.out` (no account). It
  ships `terraform fmt -check`, `validate`, `tflint`, `trivy config` (zero HIGH/CRITICAL), and
  `terraform test` with `mock_provider "aws"` for the policy tests (brief 9.1). `make plan` and
  `make plan-org` wrap the real plans for the human. Never `apply`.
- **AD13 Ports and addresses.** work 8781, personal 8782. Local profile binds 127.0.0.1. aws
  profile binds the Tailscale IPv4. Syncthing 22000/22001 (AD8). GUIs 8384/8385 on loopback.
- **AD14 `/health` contract (additive).** Per-daemon response:
  `{"ok": true, "mode": "<mode>", "modes": {"<mode>": {...}}, "deployment": "<profile>"}`. The
  `modes` map keeps its shape so the existing client parser keeps working; only the daemon's own
  mode is present.
- **AD15 `needs_mode` contract (additive).** `/utterance` returns
  `{"reply", "mode_used", "needs_mode", "suggested_mode"}`; `suggested_mode` is the other mode name
  or null. The client re-sends to the other endpoint only after spoken confirmation, with
  `mode_confirmed=true`.
- **AD16 Windows client config.** `client.yaml` gains `profile: aws | wsl` (default `wsl` when the
  file predates this change, `aws` for new installs by `install.ps1`), `api_url_work`,
  `api_url_personal`, `token_source: secretsmanager | file`, `aws_profile`, `token_secret_work`,
  `token_secret_personal`. Legacy `api_url` and `token_path` stay for `wsl`. Tokens from Secrets
  Manager come from `aws secretsmanager get-secret-value --profile <p> --query SecretString
  --output text` via subprocess (AWS CLI v2, installed by `install.ps1`), held in memory, never
  written, refetched on a 401. Two `JarvisApi` instances, two SSE streams, per-mode health.
  The `wsl` profile keeps `sleep infinity` keepalive; `aws` profile skips it.
- **AD17 Instance layout.** Users `jarvis-work` (uid 2001) and `jarvis-personal` (uid 2002),
  homes 0700, shell `/usr/sbin/nologin` for login but `sudo -u` and SSM `runAs` work. Releases at
  `/opt/jarvis/releases/<sha>/` (0755 root, venv inside), `/opt/jarvis/current` symlink,
  `/opt/jarvis/bin/` root tools (`jarvis-secrets`, `jarvis-deploy`, `jarvis-status`,
  `jarvis-vault-commit`, `post-boot-assert`). Vault `/home/jarvis-<mode>/vault`, env
  `/home/jarvis-<mode>/.jarvis/env`, token `/home/jarvis-<mode>/.jarvis/api_token`. Per-user
  Claude config lives in that user's `~/.claude` and `~/.claude.json`.
- **AD18 Bootstrap payload.** user_data stays under 16 KB: it installs packages, fetches
  `bootstrap/<sha256>/bootstrap.tar.gz` from the artifacts bucket (uploaded by Terraform from
  `ops/aws/` with the `archive` provider), and runs `bootstrap.sh`. `ops/aws/` is the single
  source of truth for units, scripts, iptables rules, Syncthing config templates, and the CloudWatch
  agent config. `jarvis-deploy` re-installs `ops/aws/` from the release it deploys, so unit changes
  ship with a release and do not need a new instance.
- **AD19 SSM documents.** The `ssm/` module takes `documents = { name = file(...) }` from
  `envs/prod`, sourced from `ops/aws/ssm/<name>.sh`. Phase 2 creates those four files as explicit
  stubs (`exit 1` with a "not implemented" message); Phase 4 replaces them. `jarvis-deploy` takes
  parameter `Sha`; `jarvis-restart` takes `Mode`; the others take none.
- **AD20 Tags and naming.** Every resource: `app=jarvis`, `env=prod`, `owner=dushan`. Names are
  prefixed `jarvis-`. Region variable `aws_region` default `us-east-2`. Instance type variable
  `instance_type` default `t4g.medium`; AMI is chosen by architecture derived from the type
  (`arm64` for `t4g.*`, else `x86_64`).
- **AD21 Two AWS profiles.** `management` profile for `org/` (assumed `sparko`; the human confirms)
  and `jarvis-prod` (IAM Identity Center) for `bootstrap/` and `envs/prod`. Terraform never stores
  profile names in code; `AWS_PROFILE` is set by the human or the Makefile target.

### 3.1 Answers to the design's open questions (decided by the planner, 2026-09-25)

The architect raised twelve questions (DESIGN.md section 12). The human was not available, so
these are planner decisions with the assumption stated; each is reversible before apply.

- **AD22 Alert email.** Terraform variable `alert_email`, required, no default. The SNS email
  subscription must be confirmed by clicking the link; the runbook says so.
- **AD23 Claude Code sign-in.** Per-user `claude login` through `scripts/oauth-login.sh`
  (credentials in that user's `~/.claude`). A per-mode `ANTHROPIC_API_KEY` in the mode secret is
  supported as an alternative (it is already in PASSTHROUGH). `jarvis/shared` holds nothing by
  default; `JEV_API_URL`/`JEV_API_KEY` may go there if one Jev key serves both modes.
- **AD24 Tailscale ACL.** The module is opt-in (`manage_tailscale_acl`, default `false`) because
  the ACL is one document for the whole tailnet. When enabled it applies
  `infra/modules/tailscale_acl/policy.hujson`, a human-owned file seeded with: `tagOwners` for
  `tag:jarvis`; grants from the workstation group to `tag:jarvis` on tcp 8781, 8782, 22000,
  22001 and udp 22000, 22001; no other access to `tag:jarvis`; `tag:jarvis` initiates nothing.
  DERP relay fallback is acceptable (end-to-end encrypted); a direct path is expected because
  the instance has a public IP and outbound UDP 41641 is open.
- **AD25 Permission sets.** Three (JarvisClient, JarvisOperator, JarvisAdmin) as the architect
  proposed. Terraform in `org/` defines them behind `create_permission_sets` (default `false`)
  because it touches the existing Identity Center instance; the runbook gives the manual
  alternative. Recommend a dedicated Identity Center user for the Windows workstation that holds
  only JarvisClient.
- **AD26 CloudTrail.** No trail is created (not in the brief). The SCP deny on disabling
  CloudTrail stays as the brief requires. Gate 1 checks read-only whether an organization trail
  exists; the answer goes into the runbook as a recommendation for the human.
- **AD27 Third SCP.** A guardrail SCP that denies `iam:CreateUser`, `iam:CreateAccessKey`,
  `ec2:CreateKeyPair`, `ec2:ImportKeyPair`, and `ec2:AuthorizeSecurityGroupIngress` is included
  behind `attach_guardrail_scp` (default `false`), recommended on in the runbook. It enforces
  brief rule 0.3 at the account boundary.
- **AD28 Region-deny exemptions.** Follow the brief and the AWS Control Tower region-deny list,
  including `kms:*`. The dry run is `terraform plan` plus IAM Access Analyzer `validate-policy`
  on the SCP document (a read-only API call that creates nothing). Attachment stays behind
  `attach_region_scp` (default `false`).
- **AD29 Elastic IP and source-IP pinning.** Allocate an EIP (same cost as the public IPv4
  already budgeted). Every instance-role permission policy gets an explicit Deny for all actions
  when `aws:SourceIp` is not the EIP and `aws:ViaAWSService` is `false`, so stolen instance
  credentials are useless off the box. No VPC endpoints may be added later without revisiting
  this.
- **AD30 Operations defaults.** Backup windows 08:00 UTC daily and 09:00 UTC Sunday as
  variables. First apply is two-step: `instance_enabled = false`, populate the four
  human-written secrets (`jarvis/work`, `jarvis/personal`, `jarvis/shared`, `jarvis/tailscale`;
  the two `api-token` secrets are written by the instance), then `instance_enabled = true`.
  Known coupling (DESIGN.md C15): the AD29 Deny also covers the SSM agent, so any change to the
  egress source address (NAT, VPC endpoint, IPv6) breaks SSM, logs, and secrets at once; the
  runbook must say so. `unattended-upgrades` security-only with automatic reboot at
  09:30 UTC (variable), after the backup windows.
- **Cost note.** At $40.59 the 50% budget alert fires every month by design; the brief fixes
  the thresholds, so the runbook explains the expected monthly email rather than changing them.

### 3.2 Amendments from the Gate 1 review (2026-09-25)

These tighten earlier decisions; where they conflict with the text above, this section wins.

- **AD2 and AD11 tightened (off-box content).** Third-party loggers `httpx`, `httpcore`,
  `telegram`, `slack_bolt`, `slack_sdk`, `mcp`, `urllib3`, `googleapiclient` are pinned to
  WARNING (httpx logs full request URLs, which carry the Telegram bot token). The JSON formatter
  passes `msg` through `redact()` and, under the aws profile, never emits `exc_text`, `exc_info`,
  or a traceback: only `error_class`. `jarvis-logexport@` forwards only lines that parse as a JSON
  object and drops everything else, counting drops in its own `nonjson_dropped` metadata line.
  `log.exception` calls in code paths that touch MCP args or bodies become `log.error` with
  `error_class=type(e).__name__`. Tests cover all three.
- **AD17 tightened (directory modes).** `/opt/jarvis/bin` is 0755 and holds only root-run
  tools, each 0700. Tools a mode user executes (`jarvis-vault-commit`, `jarvis-write-env`,
  `jarvis-syncthing-render`) live in `/opt/jarvis/libexec`, 0755, files 0755.
- **AD31 Root never follows a mode user's paths.** Every root tool that touches anything under
  `/home/jarvis-<mode>` does so as that user through `runuser -u jarvis-<mode> --`. That covers
  the Syncthing config render (runs entirely as the user, no `+` prefix; `tailscale ip -4` works
  unprivileged), `make sync-agents` (rsync as the user), token rotation (`rm` as the user), and
  outbox counts in `jarvis-status`. Anything that must stay root opens with `O_NOFOLLOW` and
  refuses symlinks. The env-file write in `jarvis-secrets sync` already drops privileges.
- **AD8 tightened.** The Syncthing render exits non-zero when `tailscale ip -4` is empty or not
  in `100.64.0.0/10`, and the unit has `ExecStartPre=` on the render so Syncthing never starts
  without a rendered config (no default listen on 0.0.0.0).
- **AD24 tightened (tailnet exposure).** Applying the ACL policy (by Terraform or by pasting
  `policy.hujson` into the admin console) is a runbook precondition before the first boot, with a
  verification step. Bootstrap masks `ssh.service`, removes `ec2-instance-connect`, and
  `post-boot-assert` includes port 22 in the no-listener check.
- **AD32 Process visibility.** `/proc` is mounted with `hidepid=invisible` and `gid=` a
  `procadm` group that only root tools use, so interactive shells started by `oauth-login.sh` or
  `sync-agents` cannot read the other mode's `claude -p` command lines. Units keep
  `ProtectProc=invisible` as well.
- **IMDS guard.** Asserts `id -u jarvis-work` is 2001 and `jarvis-personal` is 2002 before
  testing, and requires `curl` exit code 7 (connection refused) from each user, not just "failed".
- **Bootstrap ordering.** A failed first-boot `jarvis-deploy` is logged and bootstrap continues
  to enabling units and running `post-boot-assert`; the script exits non-zero at the end so the
  failure is visible in cloud-init output and CloudWatch.
- **Deploy test HOME.** The on-box test run gets a temp HOME created with `install -d -o
  jarvis-build -m 0700` and removed in a trap.
- **IAM fixes (DESIGN-IAM.md).** `aws:PrincipalArn` patterns use
  `.../sso.amazonaws.com/*AWSReservedSSO_<Set>_*` (Identity Center in us-east-1 has no region
  path segment); the builder confirms the Identity Center region. Artifacts and state bucket
  policies add a Deny when `s3:x-amz-server-side-encryption` is present and not `aws:kms`, and a
  Deny when it is `aws:kms` and the key-id header is null. SNS policy statements get
  `aws:SourceAccount` and `aws:SourceArn` conditions.
- **Policy test precision.** Every wildcard allowlist row states (operator, condition key, exact
  value). All IAM policies are built with `jsonencode()` locals, never
  `data.aws_iam_policy_document` (mock providers randomise that data source's `json`, making the
  test vacuous). The tests run with `create_permission_sets = true`. `no_ingress` matches
  `aws_security_group.*.ingress`, `aws_security_group_rule` with `type = "ingress"`,
  `aws_vpc_security_group_ingress_rule`, `aws_default_security_group` ingress, and also asserts
  the absence of `aws_key_pair`, `key_name`, `aws_iam_user`, `aws_iam_access_key`, and
  `aws_secretsmanager_secret_version`.
- **CloudTrail (AD26 resolved).** An organization trail exists: `management-events` in the
  management account, multi-region, home us-east-1. `jarvis-prod` needs no trail; the SCP deny
  is defense in depth. The runbook says so.
- **Residual risk to record.** The `sparko` profile is a long-lived IAM user key that can assume
  `OrganizationAccountAccessRole` into `jarvis-prod`. The runbook's first step recommends moving
  management-account access to Identity Center before the first apply; this is the human's call.
- **Region SCP consequence.** Every CLI caller (Windows client, `user_data`, Makefile, scripts)
  passes `--region us-east-2` explicitly.
- **Bucket key-pinning Deny (re-verification HIGH).** `DenyWrongKmsKey` on both buckets uses
  `"Null": {"s3:x-amz-server-side-encryption-aws-kms-key-id": "false"}` plus `StringNotEquals`
  against the full key ARN. Never `...IfExists` inside a Deny: it evaluates true when the header
  is absent and would block header-less `PutObject`, every multipart `UploadPart`, and the
  Terraform state and lock writes. Callers naming the key must send the full ARN.
- **IMDS guard ordering (re-verification HIGH).** At bootstrap step 4a, before users exist, run
  the rules-only guard (`iptables -C`, `ip6tables -C`, root can reach IMDS). The full guard
  (uid assert 2001/2002 plus `curl` exit 7 per user) runs after the users are created and at
  every boot through `jarvis-imds-guard.service`. The guard script takes `--rules-only`.
- **hidepid details.** Render the fstab line with the numeric gid; add
  `SupplementaryGroups=procadm` drop-ins for `systemd-logind` and, if present, `polkitd`.
- **Temp dirs.** Never `mktemp -u` followed by `install -d` (check-then-create race); use
  `mktemp -d` then `chown`. `jarvis-logfilter` also re-applies the AD11 key deletion as a second
  layer. The SNS key-policy statement for alarms and budgets gets `aws:SourceAccount`.
- **Small items folded into the design:** `Wants=network-online.target` alongside `After=`;
  `tailscale up --accept-dns=false` (egress 53 is VPC-resolver only); workstation Syncthing
  devices `introducer=false`, `autoAcceptFolders=false`; `publish-token` updates its local
  hash only after `PutSecretValue` succeeds; `org/` checks the SCP policy type is enabled before
  attaching; `LimitCORE=0` on the mode units; bootstrap treats S3 403 and 404 alike as "no
  release"; `runAsEnabled=false` on SSM is recorded as the resolution of AD17's "runAs" wording
  (interactive SSM runs as `ssm-user`, root tools go through `sudo` inside the session).

### Re-targeting decisions (2026-09-25, after the first `org/` plan; the human's call)

The human reviewed the first `org/` plan and changed two requirements. These win over AD20,
AD21, and any earlier text.

- **AD34 Region is us-east-1.** `aws_region` (envs/prod, bootstrap) and `home_region` (org)
  default to `us-east-1`; the region-deny SCP allows `us-east-1`. Region literals leave every
  script, the Makefile, the client, ops, and docs: the Makefile exports `AWS_REGION ?= us-east-1`
  to `scripts/*`; on the instance, cloud-init writes the template's `region` variable to
  `/etc/jarvis/region` and `ops/aws` tools read it; the Windows client gets an `aws_region` key
  (default `us-east-1`). Terraform test fixtures and allowlist rows follow the variable.
  Identity Center is already in us-east-1, which removes the cross-region SSO wrinkle.
- **AD35 A deletable OU, not a bare account.** `org/` creates an OU named `jarvis` under the
  organization root, creates the `jarvis-prod` account inside it with `close_on_deletion = true`,
  and attaches the SCPs to the OU (not the account). Teardown is `terraform destroy` in `org/`
  after `envs/prod` and `bootstrap` are gone: it closes the account (90-day AWS recovery window)
  and deletes the OU.
- **AD36 The management account holds nothing.** `org/` uses local Terraform state
  (`infra/org/terraform.tfstate`, gitignored, backed up by the human) instead of an S3 bucket in
  the management account; `backend.hcl` is no longer used there. The management identity
  (`sparko`) is used only to plan and apply `org/` (create, and later destroy). Identity Center
  permission sets and assignments are org-level by nature and stay in `org/`. The empty bucket
  `sparko-org-tfstate-080109295043` created earlier in the management account is deleted by the
  human. Everything else (state bucket, artifacts, instance, operations) lives in `jarvis-prod`.

## 4. Phases, owners, and gates

Fable dispatches, reads results, and decides. Builders never call `terraform apply`, never write
secret values, never open inbound ports, never create IAM users or keys, never add an AWS service
outside the brief without stopping to ask.

| Phase | Owner (model) | Deliverable | Gate |
|---|---|---|---|
| 0 | workspace-prep (Sonnet, general-purpose) | `.venv` (3.12), baseline `pytest` result, Terraform >= 1.10, tflint, trivy installed | `[x]` done 2026-09-25 |
| 1 | infra-architect (Opus) | `infra/DESIGN.md`, `infra/DESIGN-IAM.md` | `[x]` SIGN-OFF 2026-09-25 after two fix rounds (4+2 HIGH, 9 MEDIUM fixed; 1 LOW in `TODO.md`) |
| 2 | terraform-engineer (Opus) | `infra/**`, `.terraform.lock.hcl`, `infra/tests/*.tftest.hcl`, `make tf-check` | `[x]` SIGN-OFF 2026-09-25 after two fix rounds (3 HIGH, 3 MEDIUM, 7 LOW fixed; mutation-tested; 13 tests, tflint and trivy clean) |
| 3a | bootstrap-engineer (Sonnet) | `ops/aws/**` (bootstrap.sh, units, iptables, syncthing, CW agent config, logexport, vault-commit, post-boot-assert), `infra/modules/compute/templates/user_data.sh.tftpl` | `[x]` SIGN-OFF 2026-09-25 after three fix rounds (1 CRITICAL, 6 HIGH, 11 MEDIUM, LOWs fixed; 24 ops tests; one pipefail one-liner applied after sign-off) |
| 3b | app-engineer (Opus) | per-mode daemon, profiles, bind, tokens, logsetup + filter, conflict gate, gmail_watch, secrets_check, `ops/jarvis@.service`, `install_wsl.sh` update, tests | `[x]` SIGN-OFF 2026-09-25 after one fix round (1 HIGH, 4 MEDIUM, 3 LOW fixed; 453 passed, 92% coverage; 4 LOW in `TODO.md`) |
| 4 | deploy-engineer (Sonnet) | `Makefile`, `scripts/release.sh`, `scripts/ssm-run.sh`, `ops/aws/ssm/*.sh` (real), `ops/aws/lib/install-release.sh`, `scripts/oauth-login.sh`, `scripts/sync-agents.sh`, `requirements-lock.txt`, Windows client changes + tests, deploy rollback test | `[x]` SIGN-OFF 2026-09-25 after two fix rounds (8 HIGH, 10 MEDIUM, LOWs fixed; every aws call within JarvisOperator; 513 tests) |
| 5 | docs-writer (Sonnet; final fixes by Opus after a Sonnet rate limit) | `README.md` deployments section, `infra/RUNBOOK.md`, `infra/RUNBOOK-ops.md` | `[x]` SIGN-OFF 2026-09-25 after one fix round (3 MEDIUM, 6 LOW fixed; 1 LOW in `TODO.md`) |

Ordering: 0 and 1 run in parallel. After the Phase 1 gate, 2, 3a, and 3b run in parallel (their
interfaces are fixed above). 4 starts after 3b passes its gate (the client depends on AD14 to
AD16) and after 2 (the SSM stubs exist). 5 runs last.

Every gate: findings with severity; HIGH and CRITICAL are fixed by the owning builder before the
phase closes; MEDIUM is fixed when cheap, otherwise recorded in `TODO.md`; durable pitfalls go to
`CLAUDE.md`. The reviewer confirms brief rules 0.1 to 0.5 for the phase.

## 5. Test matrix (brief section 9)

| Brief | Where |
|---|---|
| 9.1 validate, tflint, scan, policy test | `make tf-check`; `infra/tests/no_ingress.tftest.hcl`, `infra/tests/iam_wildcards.tftest.hcl` (mock provider); allowed `Resource: "*"` statements listed in DESIGN.md section "IAM wildcard allowlist" |
| 9.2 post-boot assertions | `ops/aws/post-boot-assert.sh`, run by cloud-init at the end and by `jarvis-status --assert` |
| 9.3 per-mode loader | `tests/test_load_mode.py` (other env file is a tripwire: unreadable and its content must never appear) |
| 9.3 API refuses without Tailscale IP | `tests/test_api_bind.py` (mock `tailscale ip -4` failure and success) |
| 9.3 log filter | `tests/test_logfilter.py` |
| 9.3 conflict file blocks | `tests/test_outbox_conflict.py` |
| 9.4 existing suite | unchanged files must pass; `load_modes` call sites updated only |
| 9.5 deploy rollback | `tests/test_deploy_script.py` runs `ops/aws/ssm/jarvis-deploy.sh` against a temp `JARVIS_ROOT` with PATH shims for `systemctl`, `aws`, `curl`, `tailscale`; a release whose health check fails leaves `current` pointing at the previous sha |
| watcher | `tests/test_gmail_watch.py` (mock service, dedup, no body fetch, error event) |
| secrets check | `tests/test_secrets_check.py` (shared exemption, violation returns names never values) |
| client | `windows_client/tests/test_config.py` (profiles), `test_api_client.py` (two instances, in-memory token, 401 refetch), `test_flow_utterance.py` (`suggested_mode` re-send after confirm) |

## 6. Definition of done mapping

Items 1 to 5 of brief section 10 need the real instance; the runbook lists them as the
acceptance checklist. Item 6 (cost under $60) is verified in DESIGN.md during Phase 1.

## 7. Open items for the human

- `[x]` Management-account profile for `org/` is `sparko` (account 080109295043, org
  `o-lcwqey40mr`), confirmed read-only in Phase 0.
- `[!]` Re-login the `default` AWS profile (expired SSO grant) or ignore it; nothing here uses it.
- `[!]` Install the Session Manager plugin in an interactive terminal:
  `brew install --cask session-manager-plugin` (it prompts for sudo). `make oauth-login`,
  `make sync-agents`, and the SSM shell in the runbook need it.
- `[!]` Create the IAM Identity Center permission set for `jarvis-prod` (SSM, Secrets Manager read
  on the two token secrets, S3 write on the artifacts bucket). Terraform in `org/` proposes it;
  the human applies.
- `[!]` After `org/` apply: configure `AWS_PROFILE=jarvis-prod`, run `make plan-bootstrap`, apply,
  then `make plan`, review, apply, then follow the runbook.
