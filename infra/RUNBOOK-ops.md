# Jarvis on AWS: runbook (operations)

Continuation of [`infra/RUNBOOK.md`](RUNBOOK.md) sections 1 to 6. Commands here follow the same
rule: verbatim from this repo unless marked "AWS CLI generic" (a standard, documented AWS CLI
call this repo has no wrapper script for).

## 7. Rotations

### 7.1 Tailscale key

1. Generate a new one-off key in the Tailscale admin console: **Reusable: off, Ephemeral: off,
   Pre-authorized: on, Tags: `tag:jarvis`, Expiry: 1 day** (same settings as the first join,
   RUNBOOK.md section 3).
2. Copy `{"authkey": "tskey-auth-<value>"}` (with the new key) to the clipboard, then write it
   to `jarvis/tailscale` from stdin (never a heredoc, argv, or a file in the repo; DESIGN.md
   section 5):
   ```
   pbpaste | AWS_PROFILE=jarvis-operator aws secretsmanager put-secret-value --secret-id jarvis/tailscale --secret-string file:///dev/stdin --region us-east-2
   ```
   On Windows, run it from WSL (where `/dev/stdin` exists), with the Windows clipboard as the
   source. Native PowerShell is not supported for this step: the AWS CLI cannot read a blob
   parameter from stdin on Windows, and `(Get-Clipboard)` in argv would expose the value to
   other processes.
   ```
   powershell.exe -NoProfile -Command Get-Clipboard | tr -d '\r' | AWS_PROFILE=jarvis-operator aws secretsmanager put-secret-value --secret-id jarvis/tailscale --secret-string file:///dev/stdin --region us-east-2
   ```
   Clear the Windows clipboard afterwards, from WSL:
   ```
   powershell.exe -NoProfile -Command "Set-Clipboard -Value ' '"
   ```
   Expected: a JSON response with `"Name": "jarvis/tailscale"` and a new `"VersionId"`. Then
   clear the clipboard:
   ```
   pbcopy </dev/null
   ```
3. Open a root shell on the box (`ssm:StartSession` on `AWS-StartInteractiveCommand`, same
   document class `oauth-login.sh`/`sync-agents.sh` use):
   ```
   aws ssm start-session --target <instance-id> --region us-east-2 --profile jarvis-operator
   ```
4. In that session, repeat the tmpfs handling from `ops/aws/lib/tailscale-join.sh` by hand, with
   `--force-reauth` added (DESIGN.md section 5 rotation table):
   ```
   sudo install -d -m 0700 -o root -g root /run/jarvis
   ```
   ```
   sudo sh -c 'aws secretsmanager get-secret-value --secret-id jarvis/tailscale --region us-east-2 --query SecretString --output text | jq -r .authkey > /run/jarvis/ts-authkey'
   ```
   ```
   sudo tailscale up --force-reauth --auth-key=file:/run/jarvis/ts-authkey \
     --advertise-tags=tag:jarvis --hostname=jarvis --ssh=false --accept-dns=false
   ```
5. Shred the key file. Run this whether `tailscale up` succeeded or failed (the boot script does
   the same with an `EXIT` trap):
   ```
   sudo shred -u /run/jarvis/ts-authkey
   ```
   Expected: no output.
6. Confirm: `tailscale status --json` (via `make status`) shows `BackendState: Running`.

### 7.2 API tokens

```
make restart MODE=work ROTATE=true
```
This runs `jarvis-restart.sh`: `runuser -u jarvis-work -- rm -f -- .../api_token`, then
`systemctl restart jarvis@work`. Expected: `[jarvis-restart] restarted jarvis@work rotate=true`.
The daemon creates a fresh token (`O_EXCL`) and `ExecStartPost=-+jarvis-secrets publish-token
work` republishes it to `jarvis/work/api-token`. The Windows client gets `401` on its next call
and refetches from Secrets Manager automatically (AD16) -- no client-side action needed. Repeat
with `MODE=personal` for the other token. If the tray still shows 401s after a minute, restart it
manually (tray menu, or log off/on).

### 7.3 Mode secrets (`jarvis/work`, `jarvis/personal`, `jarvis/shared`)

Copy the full new JSON for the secret to the clipboard (shapes in RUNBOOK.md section 3), then:
```
pbpaste | AWS_PROFILE=jarvis-operator aws secretsmanager put-secret-value --secret-id jarvis/<name> --secret-string file:///dev/stdin --region us-east-2
```
On Windows, run it from WSL (where `/dev/stdin` exists), with the Windows clipboard as the
source. Native PowerShell is not supported for this step: the AWS CLI cannot read a blob
parameter from stdin on Windows, and `(Get-Clipboard)` in argv would expose the value to
other processes.
```
powershell.exe -NoProfile -Command Get-Clipboard | tr -d '\r' | AWS_PROFILE=jarvis-operator aws secretsmanager put-secret-value --secret-id jarvis/<name> --secret-string file:///dev/stdin --region us-east-2
```
Clear the Windows clipboard afterwards, from WSL:
```
powershell.exe -NoProfile -Command "Set-Clipboard -Value ' '"
```
Expected: a JSON response with `"Name": "jarvis/<name>"` and a new `"VersionId"`. The value
travels by pipe, never a heredoc, here-string, argv, or a file in the repo (DESIGN.md section 5).
Clear the clipboard:
```
pbcopy </dev/null
```
Then:
```
make secrets-sync
```
Expected: `[jarvis-secrets-sync] changed_modes:work` (or `personal`, or both -- only the mode(s)
whose env actually changed are restarted; a `jarvis/shared` change restarts both). If nothing
changed: `[jarvis-secrets-sync] no env changes, nothing to restart`. A shared-value violation
(the same secret value in both mode secrets under different or same keys, outside
`jarvis/shared`) makes `jarvis-secrets sync` write nothing and exit 1 -- see "unit fails because
`jarvis-secrets` found a shared secret" in section 12.

### 7.4 Claude Code and MCP OAuth

Tokens live on the box (`~/.claude*` per user, on EBS), never in Secrets Manager. Revoke at the
provider, then re-run:
```
make oauth-login MODE=work
```
or `MODE=personal` (RUNBOOK.md section 5).

## 8. Restore from a snapshot

AWS CLI generic from here (no repo script) -- run under `AWS_PROFILE=jarvis-prod`
(`backup:StartRestoreJob`/`ec2:*Volume*` are outside `JarvisOperator`'s scope, DESIGN-IAM.md 3.12).

1. Find the recovery point:
   ```
   aws backup list-recovery-points-by-backup-vault --backup-vault-name jarvis-backup \
     --region us-east-2 --profile jarvis-prod
   ```
2. Get a restore metadata template and edit it (availability zone, KMS key) as needed:
   ```
   aws backup get-recovery-point-restore-metadata --backup-vault-name jarvis-backup \
     --recovery-point-arn <arn> --region us-east-2 --profile jarvis-prod
   ```
3. Start the restore (creates a **new** EBS volume, does not touch the running instance):
   ```
   aws backup start-restore-job --recovery-point-arn <arn> \
     --iam-role-arn arn:aws:iam::<account-id>:role/jarvis-backup \
     --metadata file://restore-metadata.json --resource-type EBS \
     --region us-east-2 --profile jarvis-prod
   ```
   Poll `aws backup describe-restore-job --restore-job-id <id> --region us-east-2 --profile
   jarvis-prod` until `Status: COMPLETED`; note the new `CreatedResourceArn` (a volume id).
4. Stop the instance, swap the root volume, restart it:
   ```
   aws ec2 stop-instances --instance-ids <instance-id> --region us-east-2 --profile jarvis-prod
   ```
   ```
   aws ec2 wait instance-stopped --instance-ids <instance-id> --region us-east-2 --profile jarvis-prod
   ```
   ```
   aws ec2 detach-volume --volume-id <old-volume-id> --region us-east-2 --profile jarvis-prod
   ```
   (the old volume is not deleted -- `delete_on_termination = false` -- keep it until the restore
   is confirmed good)
   ```
   aws ec2 attach-volume --volume-id <new-volume-id> --instance-id <instance-id> \
     --device /dev/sda1 --region us-east-2 --profile jarvis-prod
   ```
   ```
   aws ec2 start-instances --instance-ids <instance-id> --region us-east-2 --profile jarvis-prod
   ```
5. Verify, back under `jarvis-operator`:
   ```
   make status
   ```
   ```
   aws ssm start-session --target <instance-id> --region us-east-2 --profile jarvis-operator \
     --document-name AWS-StartInteractiveCommand --parameters command="sudo /opt/jarvis/bin/jarvis-status --assert"
   ```
   Expected, last line: `post-boot-assert: all checks passed`.

## 9. Alarm drills

Also the acceptance test for the `AlarmsAndBudgetsToSns` KMS key statement's `aws:SourceAccount`
condition (TODO.md, Gate 1 finding): that condition is confirmed for Budgets but was not
separately confirmed for CloudWatch alarms publishing to the KMS-encrypted `jarvis-alerts` topic.
This drill proves it either way.

1. Stop the daemon on purpose:
   ```
   aws ssm start-session --target <instance-id> --region us-east-2 --profile jarvis-operator \
     --document-name AWS-StartInteractiveCommand --parameters command="sudo systemctl stop jarvis@work"
   ```
2. Wait. `jarvis-heartbeat-work` alarms after 3 missing 300 s periods (up to 15 minutes),
   `treat_missing_data = breaching`. Expect an email at `alert_email` with the alarm in `ALARM`
   state.
   - **If no email arrives** but the CloudWatch console shows the alarm went to `ALARM`: the SNS
     publish from `cloudwatch.amazonaws.com` is being blocked by the KMS key policy's
     `aws:SourceAccount` condition on `AlarmsAndBudgetsToSns` (W6, DESIGN-IAM.md 3.3). Per
     TODO.md, the fix is to drop that condition for the `cloudwatch.amazonaws.com` principal only
     (Budgets keeps it).
3. Restart it:
   ```
   make restart MODE=work
   ```
4. Confirm the alarm returns to `OK` (also emailed) within the next evaluation period.

## 10. Full teardown

Destructive and mostly manual by design (brief rule 0.1: this repo never runs `apply` or
`destroy` for you). Order matters: `envs/prod`, then `bootstrap/`, then `org/`.

### 10.1 `envs/prod`

The instance has `disable_api_termination = true` and its root volume has
`delete_on_termination = false` (DESIGN.md section 1) -- both deliberate brakes on accidental
destroy. AWS CLI generic, under `AWS_PROFILE=jarvis-prod`:
```
aws ec2 modify-instance-attribute --instance-id <instance-id> --no-disable-api-termination \
  --region us-east-2 --profile jarvis-prod
```
Empty the artifacts bucket (no `force_destroy`; every object version must go, AWS CLI generic):
```
aws s3api list-object-versions --bucket jarvis-artifacts-<account-id> --output json \
  --profile jarvis-prod --region us-east-2 \
  | jq '{Objects: [(.Versions // [])[], (.DeleteMarkers // [])[] | {Key, VersionId}], Quiet: true}' \
  | aws s3api delete-objects --bucket jarvis-artifacts-<account-id> --delete file:///dev/stdin \
      --profile jarvis-prod --region us-east-2
```
**Required:** delete every recovery point in the `jarvis-backup` vault. The vault has no
`force_destroy` (`infra/modules/backup/main.tf`), and AWS refuses to delete a vault that still
holds recovery points, so `destroy` fails without this step. AWS CLI generic:
```
aws backup list-recovery-points-by-backup-vault --backup-vault-name jarvis-backup --query 'RecoveryPoints[].RecoveryPointArn' --output text --profile jarvis-prod --region us-east-2
```
Expected: one or more `arn:aws:ec2:us-east-2::snapshot/snap-...` ARNs. For each ARN:
```
aws backup delete-recovery-point --backup-vault-name jarvis-backup --recovery-point-arn <arn> --profile jarvis-prod --region us-east-2
```
Expected: no output. Re-run the list command until it prints nothing. Then:
```
AWS_PROFILE=jarvis-prod terraform -chdir=infra/envs/prod destroy
```
The root volume (`delete_on_termination = false`) survives as an orphaned EBS volume -- delete it
by hand once you are sure you do not need it (`aws ec2 delete-volume --volume-id <id> ...`).

CloudWatch log groups (`/jarvis/work`, `/jarvis/personal`, `/jarvis/cloud-init`) **are** deleted
immediately by this `destroy` -- unlike the recovery points, there is no separate "log group
still holds data" protection. If you want the last 30 days retained for a while after teardown,
skip destroying the `observability` module's log groups (a targeted `terraform state rm` before
destroy, or a partial `-target` destroy) instead of tearing down all of `envs/prod` at once.

### 10.2 `bootstrap/`

The state bucket resource has `lifecycle { prevent_destroy = true }` (`infra/bootstrap/main.tf`).
Empty it first (same versioned-bucket pattern as 10.1, bucket name from `terraform output -raw
state_bucket`), then temporarily remove that `prevent_destroy` block, destroy, and revert the
edit (`git checkout infra/bootstrap/main.tf`) once done -- it is a deliberate one-time override,
not a permanent relaxation:
```
AWS_PROFILE=jarvis-prod terraform -chdir=infra/bootstrap destroy
```

### 10.3 `org/`

Set `attach_region_scp = false` and `attach_guardrail_scp = false` in
`infra/org/org.auto.tfvars` if either was ever turned on, then `make plan-org` and apply -- this
cleanly detaches those two SCPs. `jarvis-org-guard` stays attached unconditionally (it is not
gated by a variable in `infra/org/main.tf`); it stops applying only once the account is removed
from the organization.

`aws_organizations_account.jarvis_prod` has `lifecycle { prevent_destroy = true }` and
`close_on_deletion = false`: a plain `terraform destroy` will refuse, and even after removing that
block, destroying the *resource* only detaches it from Terraform state -- it does **not** close
the real AWS account. Closure is a separate, manual, AWS CLI generic step:
```
aws organizations close-account --account-id <account-id> --profile sparko
```
Only do this once you are certain `envs/prod` and `bootstrap/` are fully torn down -- account
closure is not reversible for 90 days and nothing inside a closed account is reachable.

### 10.4 Tailscale and Syncthing on the workstation

- **Tailscale node:** in the Tailscale admin console, Machines, delete the `jarvis` machine
  (there is nothing left on the box to log it out from once the instance is gone).
- **Syncthing devices:** on the workstation, open `http://127.0.0.1:8384`, remove the
  `jarvis-server` (or however you named it) device entries for both the work and personal
  pairings (Actions > matching device > Remove Device), or delete them over the REST API
  (`DELETE http://127.0.0.1:8384/rest/config/devices/<id>`, workstation tool, same auth as
  `install.ps1`'s own calls). This does not touch anything on the (already torn down) server.

## 11. Acceptance checklist

Brief section 10, items 1 to 6, plus the TODO.md `VERIFY` item and the alarm-drill double duty
noted in section 9.

- [ ] **1. Both services survive a reboot with no manual steps.** Reboot the instance
  (`aws ec2 reboot-instances --instance-ids <id> --region us-east-2 --profile jarvis-prod`, AWS
  CLI generic), wait, then `make status` shows `jarvis@work.service: active` and
  `jarvis@personal.service: active` with no SSM shell or manual restart in between.
- [ ] **2. Ctrl+Alt+W / Ctrl+Alt+P reach the daemons over Tailscale.** From the Windows tray with
  the `aws` client profile installed: press each hotkey, confirm a beep and a spoken reply (not a
  "Jarvis is offline" message).
- [ ] **3. Obsidian shows Jarvis's edits within a minute.** Have either mode's agent write to its
  vault (e.g. approve a draft), confirm the change appears in Obsidian on the workstation within
  about 60 seconds (Syncthing pairing from RUNBOOK.md section 6).
- [ ] **4. OFW notification -> Telegram push -> approved send.** Trigger (or wait for) an OFW
  email; confirm a Telegram message with a draft id arrives, then `approve <id> <hash>` sends it
  through the OFW MCP.
- [ ] **5. `make status` is green, and the heartbeat alarms fire on a deliberate stop.** Section 9
  ("Alarm drills"), both directions (ALARM, then OK).
- [ ] **6. Monthly cost is under the $60 budget.** DESIGN.md section 9 estimates **$40.59** (68%
  of budget). The 50% budget alert (~$30) fires **every month by design** -- the steady-state bill
  sits above that threshold; this is expected, not a fault. Confirm the actual bill in Cost
  Explorer stays under $60 after the first full month.
- [ ] **TODO.md VERIFY: no `token_publish_failed` on first boot.** Check both log groups after the
  first deploy. These use `jarvis-prod` because the `JarvisOperator` permission set
  (`infra/org/policies.tf`) grants no CloudWatch Logs read:
  ```
  aws logs filter-log-events --log-group-name /jarvis/work --filter-pattern '{ $.event = "token_publish_failed" }' --region us-east-2 --profile jarvis-prod
  ```
  ```
  aws logs filter-log-events --log-group-name /jarvis/personal --filter-pattern '{ $.event = "token_publish_failed" }' --region us-east-2 --profile jarvis-prod
  ```
  (AWS CLI generic.) Expected: no events. If either appears, the `EncryptApiTokens` KMS grant
  (`kms:GenerateDataKey`, `kms:Decrypt`, DESIGN-IAM.md 3.2, Phase 2 amendment K1) is still too
  narrow for the token secrets.

## 12. Troubleshooting

**Unit fails because `jarvis-secrets` found a shared secret.**
`systemctl status jarvis-secrets.service` (over an SSM shell) shows a non-zero exit. Its messages
are only in that unit's journal on the box (`journalctl -u jarvis-secrets` in the SSM session), not
in the `/jarvis/<mode>` log groups (`jarvis-logexport@` follows only `jarvis@<mode>`). The journal
has one of: `secrets_unpopulated name=jarvis/<name>` (a value secret has no version yet -- go back
to RUNBOOK.md section 3), `secret_invalid_keys mode=<mode> keys=...` (bad key format, non-string
value, or embedded CR/LF), or `shared_violation keys=...` (the same value appears in both mode
secrets outside `jarvis/shared`). Fix the named secret with another `put-secret-value`, then
`make secrets-sync`. `jarvis@work`/`jarvis@personal` will not start on their own
(`Requires=jarvis-secrets.service`) until this succeeds.

**Daemon refuses to start because Tailscale is down.**
`api.bind: tailscale` (AD5) means the daemon calls `tailscale ip -4` at startup and treats a
non-zero exit, empty output, or a non-`100.64.0.0/10` address as a startup error -- never a
fallback to `0.0.0.0`. Check `tailscale status --json` (`make status`, or an SSM shell). A
`BackendState` other than `Running` means the tailnet key expired or the box lost its join;
re-authenticate with the rotation procedure in section 7.1 (a fresh key, `--force-reauth`).

**`/health` unreachable from Windows.**
In order of likelihood:
1. **Tailscale ACL.** The workstation's group must be in the `src` of the one grant in
   `policy.hujson` (RUNBOOK.md section 2.5); confirm with the file's own `tests` block, or from
   another tailnet device that `jarvis:8781`/`:8782` behave as the ACL intends.
2. **MagicDNS.** The hostname `jarvis` only resolves if MagicDNS is enabled tailnet-wide and the
   workstation's Tailscale client accepts tailnet DNS (the server itself runs
   `--accept-dns=false`, which only affects the server's own resolver, not whether other devices
   can resolve *it*). If `jarvis` does not resolve, try the box's Tailscale IPv4 directly
   (`tailscale status` on any tailnet device) as a diagnostic, and fix MagicDNS in the admin
   console.
3. **Token mismatch.** A `401` should auto-refetch from Secrets Manager (AD16); a `403` instead
   means the `jarvis-client` profile's `JarvisClient` permission set assignment is missing or the
   session expired -- `aws sso login --profile jarvis-client` on the workstation.

**Syncthing conflict file blocking an outbox item.**
`approve <id> <code>` (or the voice flow) fails with `sync conflict: resolve <filename> first`
(AD9). A file matching `outbox/<id>.sync-conflict-*` (or `outbox/<id>*.sync-conflict-*`) exists in
that vault. Open it, compare against `outbox/<id>.md`, keep the correct content, delete the
conflict file, then retry the approval. (TODO.md notes the glob over-blocks on id prefixes --
fails closed, not a security issue, just occasionally over-cautious.)

**Deploy rolled back.**
`make deploy SHA=<sha>` printed `rolled_back <sha> -> <prev>` (health check failed after the
switch, both daemons back on `<prev>` and healthy) or `rollback_health_check_failed <sha> ->
<prev>` (daemons restarted on `<prev>` but `/health` still did not pass -- check manually). Either
way `releases/DEPLOYED` was **not** updated (only a passing `make deploy` writes it), so the next
plain `make deploy SHA=<same-or-newer>` retries cleanly. Investigate on the box first:
```
aws ssm start-session --target <instance-id> --region us-east-2 --profile jarvis-operator \
  --document-name AWS-StartInteractiveCommand --parameters command="sudo journalctl -u jarvis@work -u jarvis@personal --since -10min --no-pager"
```
and check `/jarvis/cloud-init` (first boot) or `/jarvis/work` / `/jarvis/personal` (later deploys)
in CloudWatch Logs, under `jarvis-prod` (the `JarvisOperator` set has no CloudWatch Logs read), for
the failing step (checksum mismatch, venv/test failure, or a failed
`/health` response body).
