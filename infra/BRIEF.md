# Brief: Move the Jarvis Backend to AWS with Terraform

This is the original task brief, verbatim. `infra/PLAN.md` is the orchestration plan derived
from it and records every place the plan deviates from, or tightens, this brief. Where this
brief conflicts with earlier docs on safety, the stricter rule wins.

---

You are working in the `jarvis/` repo. Today the backend runs locally (macOS originally, then WSL2 per the Windows port). Move the always-on backend to a dedicated AWS account, deploy everything with Terraform, and update the app configuration to match. The Windows client (tray, hotkeys, speech-to-text, text-to-speech) stays on the workstation and talks to the server over Tailscale. Local WSL mode must keep working for development.

Do not weaken any existing safety behavior: mode isolation, the outbox gates, hash-checked OFW approval, draft-only email, and read-only Gmail for the OFW watcher. Where this prompt conflicts with earlier docs on safety, the stricter rule wins.

## 0. Rules for you (Claude Code)

1. Never run `terraform apply` or `destroy`. Produce plans (`terraform plan -out`) and stop for human review. The human applies.
2. No secret values in Terraform code, variables, tfvars, or state. Terraform creates empty Secrets Manager secrets. Values are set by the human with the CLI.
3. No inbound security group rules, no SSH keys, no key pairs, no IAM users, no access keys.
4. No personal-mode content (OFW text, email bodies, transcripts) in any log that leaves the instance. Log metadata only (ids, types, durations, status).
5. Ask before adding any AWS service not listed here.

## 1. Target architecture

Dedicated AWS account `jarvis-prod` under the existing AWS Organization. Default region: us-east-2 (make it a variable).

One EC2 instance:
- t4g.medium, Ubuntu 24.04 LTS arm64. Fall back to t3.medium via a variable if any dependency lacks an arm64 build.
- 30 GB gp3 root volume, encrypted with a customer-managed KMS key.
- Public subnet with a public IPv4 address, needed for outbound egress without a NAT gateway.
- A security group with zero inbound rules. Outbound: 443 TCP, 80 TCP (package mirrors only), 41641 UDP and 3478 UDP (Tailscale), 53.
- IMDSv2 required, hop limit 1.
- SSM Session Manager for shell access.
- Auto-recovery on system status check failure.

Two Linux users, each a hard boundary:
- `jarvis-work` and `jarvis-personal`, each with its own home directory, vault, Claude Code config and MCP OAuth tokens, env file, systemd service, Syncthing instance, and API port.
- A root-only `jarvis-secrets` oneshot service syncs each mode's Secrets Manager secret to `/home/<user>/.jarvis/env` (0600, owned by that user) at boot and on demand.
- iptables rule: only root may reach IMDS (169.254.169.254). This stops either mode user from borrowing the instance role and reading the other mode's secret. Persist the rule and add a boot-time check that fails loudly if it is missing.

Networking to your devices: Tailscale. The server joins the tailnet as `tag:jarvis` with MagicDNS name `jarvis`. APIs listen only on the Tailscale interface:
- work API: port 8781
- personal API: port 8782
- Syncthing sync: 22000 TCP/UDP on the tailnet only; GUIs bound to localhost

## 2. Terraform layout

```
infra/
  bootstrap/            # run once: state bucket (S3, versioned, KMS, public access blocked)
  modules/
    network/            # VPC, one public subnet, IGW, route table, SG (egress only), VPC flow logs to S3
    compute/            # launch template, instance, EBS, KMS key, instance profile, auto-recovery alarm
    secrets/            # empty secrets: jarvis/work, jarvis/personal, jarvis/shared, jarvis/tailscale
    artifacts/          # S3 bucket for release tarballs (versioned, KMS, access only from instance role and deployer)
    backup/             # AWS Backup plan: daily EBS snapshots, 14-day retention, weekly 8-week retention
    observability/      # CloudWatch log groups (30-day retention), heartbeat alarms, SNS topic to email, budget
    ssm/                # SSM documents: jarvis-deploy, jarvis-restart, jarvis-secrets-sync, jarvis-status
    tailscale_acl/      # optional, tailscale provider: ACL only (no auth keys in state)
  envs/prod/            # composes modules, backend config, variables
  org/                  # optional, run from the management account: account creation and SCPs
```

Requirements:
- Terraform >= 1.10 with an S3 backend using native lockfile (`use_lockfile = true`), no DynamoDB.
- Pin provider versions. Add `.terraform.lock.hcl` to git.
- Tag everything with `app=jarvis`, `env=prod`, `owner=dushan`.
- `org/`: create the `jarvis-prod` account, plus two SCPs. The first denies all regions except the chosen one, with an exemption for global services (IAM, STS, Organizations, Support, CloudFront, Budgets, Route 53, KMS global calls). The second denies leaving the org and denies disabling CloudTrail. Document that region-deny SCPs have previously conflicted with AWS services that call other regions internally, so the exemption list must be tested with a plan and a dry-run before being attached.
- The instance role gets only: `AmazonSSMManagedInstanceCore`, `secretsmanager:GetSecretValue` on the four jarvis secrets, `kms:Decrypt` on the jarvis key, read on the artifacts bucket, CloudWatch Logs put on the jarvis log groups, and `cloudwatch:PutMetricData` restricted to the `Jarvis` namespace.
- Budget: $60 per month with alerts at 50%, 80%, and 100% to the SNS email.

## 3. Instance bootstrap (cloud-init via templatefile)

Keep user_data short and idempotent. It must:
1. Install packages: python3.12 venv, git, jq, unzip, awscli v2, Node LTS, Claude Code (native install), Syncthing, Tailscale, CloudWatch agent, iptables-persistent.
2. Create the two users with no login shell password. Create `/opt/jarvis/releases` and `/opt/jarvis/current`.
3. Install the IMDS iptables rule and persist it.
4. Fetch the Tailscale auth key from `jarvis/tailscale` and run `tailscale up --advertise-tags=tag:jarvis --hostname=jarvis --ssh=false`. Then delete the key from memory and disk.
5. Install systemd units: `jarvis-secrets.service` (root oneshot), `jarvis@work.service` and `jarvis@personal.service` (User=jarvis-<mode>, Restart=always, After=tailscaled and jarvis-secrets), per-user `syncthing@jarvis-<mode>.service`, and per-user `jarvis-vault-commit@<mode>.timer` (git auto-commit every 10 minutes, only if changed).
6. Configure the CloudWatch agent to ship only the jarvis journald units' metadata logs (see section 5) plus cloud-init output.
7. Pull the latest release from S3 (see section 6) if one exists.

## 4. App changes

### 4.1 One daemon per mode

- Refactor `modes.load_modes()` into `load_mode(name)`. The personal daemon never loads work config and vice versa.
- `python -m jarvis.main --mode work|personal`. Work runs the Slack DM channel and work schedules. Personal runs Telegram, personal schedules, and the OFW watcher.
- Move the "no secret shared across modes" check into `jarvis-secrets` (root, sees both secrets), with an exemption for keys under `jarvis/shared`. On violation, write nothing and fail the unit.
- The Jev mode check still runs. If an utterance sounds like the other mode, the API returns `needs_mode` with `suggested_mode`, and the Windows client re-sends it to the other endpoint only after you confirm by voice.

### 4.2 Config profiles

Add `deployment: local | aws` to `config.yaml`, with per-profile overrides in `config.aws.yaml` and `config.local.yaml`:
- aws: vault path `/home/jarvis-<mode>/vault`; env file `/home/jarvis-<mode>/.jarvis/env`; API bind address from `tailscale ip -4` resolved at startup (refuse to start if Tailscale is down, never fall back to 0.0.0.0); ports 8781 and 8782.
- local: keep the current WSL behavior.
- Replace the macOS and WSL paths in brain, outbox, and the watcher with values from the profile.

### 4.3 API tokens

Each daemon generates its API token on first start and stores it in `/home/jarvis-<mode>/.jarvis/api_token` (0600). The root `jarvis-secrets` service copies each token into a separate secret, `jarvis/<mode>/api-token`, so the Windows client can fetch it with the human's IAM Identity Center profile. Tokens never travel through chat, S3, or logs.

### 4.4 OAuth on a headless server

Add `scripts/oauth-login.sh <mode>` that:
1. Starts an SSM port-forwarding session from the workstation to the instance for the OAuth callback ports (Slack 3118, the Google callback port Claude Code uses, and 8766 for the Gmail watcher's read-only token).
2. Opens an SSM shell as `jarvis-<mode>` in the vault directory and prints the steps: run `claude`, then `/mcp`, and authorize each server.

For personal mode, also run `python -m jarvis.gmail_watch --auth` through the same forward. After login, the script asks each vault "which Gmail account am I connected to" and prints the answer for the human to verify.

### 4.5 Vault sync

- Syncthing per user, syncing that user's vault only, with devices introduced over the tailnet (MagicDNS). Relays and global discovery are disabled (tailnet only). Add `.stignore` entries for `.obsidian/workspace*.json`, `.trash/`, and `outbox/.lock`.
- Git on the server is the history: the auto-commit timer runs as each user with author `jarvis-<mode>`. Nothing is pushed anywhere off the instance. Snapshots cover durability.
- Conflict policy: Syncthing conflict files in `outbox/` block execution of that item (new executor gate) until the human resolves it.

### 4.6 Windows client

Point `client.yaml` at `http://jarvis:8781` (work) and `http://jarvis:8782` (personal). Tokens come from `aws secretsmanager get-secret-value --profile <sso-profile>` at client startup and are cached in memory only. Update `install.ps1` to install Syncthing and Tailscale and to register both vault folders. Keep the WSL backend as a dev profile.

## 5. Logging and monitoring

- The app logs structured JSON to journald: timestamp, mode, event, ids, durations, status, and error class. Add a log filter that drops any field named body, text, draft, preview, snippet, subject, or transcript. Unit-test the filter.
- CloudWatch log groups `/jarvis/work` and `/jarvis/personal` with 30-day retention, encrypted with the jarvis KMS key.
- Heartbeat: each daemon publishes `Jarvis/Heartbeat` (dimension Mode) every 5 minutes. Alarm after 15 minutes missing, sent to SNS email. Add an alarm for OFW watcher errors (`Jarvis/OfwWatchErrors` > 0 for 3 periods).
- Also alarm on EC2 status checks and on EBS volume usage above 80%, which needs the CloudWatch agent disk metric.

## 6. Deploy flow

- `make release` builds a tarball of the app and both vault templates (templates only, never live vaults), computes its sha256, and uploads both to the artifacts bucket under `releases/<git-sha>/`.
- `make deploy SHA=<git-sha>` runs the SSM document `jarvis-deploy`, which:
  1. downloads the release and verifies the checksum
  2. unpacks it to `/opt/jarvis/releases/<sha>`
  3. builds the venv
  4. runs the test suite on the box
  5. atomically switches `/opt/jarvis/current`
  6. restarts both services
  7. checks each daemon's local `/health`
  8. rolls back to the previous symlink if a health check fails
- The live vaults are never overwritten by a deploy. Template changes to `CLAUDE.md` or `.claude/agents` are applied by a separate `make sync-agents MODE=<mode>` that shows a diff and requires confirmation.
- `make status` runs `jarvis-status`: service states, last heartbeat, pending outbox counts, disk use, Tailscale status.

## 7. Subagents and dispatch

Plan first, then dispatch by capability:
- `infra-architect` (Opus): writes `infra/DESIGN.md`, covering the resource list, IAM policies in full, a cost estimate per month, and a threat model (what a compromised mode user can and cannot reach).
- `terraform-engineer` (Opus): the modules and `envs/prod`.
- `bootstrap-engineer` (Sonnet): cloud-init, systemd units, iptables, Syncthing, the CloudWatch agent config.
- `app-engineer` (Opus): the per-mode daemon split, config profiles, API binding, token handling, log filter, outbox conflict gate.
- `deploy-engineer` (Sonnet): Makefile, release packaging, SSM documents, rollback, oauth-login script, Windows client changes.
- `security-reviewer` (Opus): gates every phase. Runs `tflint`, `checkov` or `trivy config`, and reviews every IAM policy line by line. Confirms rules 0.1 through 0.5 hold.
- `docs-writer` (Sonnet): the README deploy and runbook sections.

## 8. Phases (security-reviewer sign-off between each)

1. Design: `infra/DESIGN.md`. No code.
2. Terraform: all modules, `terraform fmt`, `validate`, `tflint`, and a static scan with zero high findings. Produce `plan.out` and a human-readable plan summary, then stop for review.
3. Bootstrap and app split: cloud-init, units, the per-mode daemon, profiles, log filter, all tests green locally.
4. Deploy tooling: release, deploy, rollback, status, oauth-login, Windows client updates.
5. Runbook: first-time setup order, secret population commands, OAuth login, Syncthing pairing, rotating the Tailscale key, restoring from a snapshot, rotating the API tokens, and full teardown.

## 9. Tests

1. Terraform: `validate`, `tflint`, a static scan, and a policy test that fails if any security group has an ingress rule or any IAM policy has `*` on resources other than those documented in DESIGN.md.
2. Bootstrap: a script run on the instance after the first boot asserts that:
   - `jarvis-work` cannot read `/home/jarvis-personal` and vice versa
   - neither mode user can reach IMDS
   - the APIs are not listening on 0.0.0.0
   - Syncthing relays are off
3. App: the per-mode loader never reads the other mode's env; the API refuses to start without a Tailscale IP; the log filter drops content fields; a Syncthing conflict file blocks an outbox item.
4. All existing tests still pass: outbox gates, hash approval, CRLF, cross-mode blocking, OFW classification.
5. Deploy: a deliberately broken release fails its health check and rolls back automatically.

## 10. Definition of done

After the human applies Terraform and follows the runbook:
1. Both services survive an instance reboot with no manual steps.
2. From the workstation, Ctrl+Alt+W and Ctrl+Alt+P reach the work and personal daemons over Tailscale.
3. Obsidian on the workstation shows vault edits made by Jarvis within a minute.
4. An OFW notification email produces a Telegram push with a draft, and "approve <id> <hash>" sends it through the OFW MCP.
5. `make status` is green, and the heartbeat alarms fire when a service is stopped on purpose.
6. The monthly cost estimate in DESIGN.md is under the $60 budget.
