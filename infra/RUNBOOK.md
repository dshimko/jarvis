# Jarvis on AWS: runbook

Procedures only. Design and rationale are in `infra/DESIGN.md` and `infra/DESIGN-IAM.md`; the
build plan is `infra/PLAN.md`. Every command below exists verbatim in this repo (Makefile,
`scripts/*.sh`, `ops/aws/**`) unless marked "workstation tool" (an external CLI's own documented
usage, e.g. `tfenv`, `uv`, `syncthing cli`, `aws configure sso`).

Sections 7 to 12 (rotations, restore, alarm drills, teardown, acceptance checklist,
troubleshooting) continue in [`infra/RUNBOOK-ops.md`](RUNBOOK-ops.md).

## 1. Prerequisites (workstation)

1. **AWS CLI v2.** `aws --version` must print `aws-cli/2.x`.
2. **Session Manager plugin.** Required by every `make oauth-login`, `make sync-agents`, and any
   interactive SSM shell below. Install interactively (it prompts for `sudo`):
   ```
   brew install --cask session-manager-plugin
   ```
   Verify:
   ```
   session-manager-plugin --version
   ```
3. **Terraform >= 1.10**, selected through `tfenv` (workstation tool; this workstation uses
   1.16.4):
   ```
   tfenv install 1.16.4
   tfenv use 1.16.4
   terraform version
   ```
4. **tflint**, installed at `~/.local/bin/tflint` (no Homebrew formula). Make sure that directory
   is on `PATH` before running `make tf-check`:
   ```
   export PATH="$HOME/.local/bin:$PATH"
   tflint --version
   ```
5. **Python 3.12 venv** for the test suite (`make test`), created with `uv` (workstation tool;
   the uv-managed `python3.12 -m venv` cannot bootstrap pip on its own):
   ```
   uv venv .venv --python 3.12 --seed
   uv pip install --python .venv/bin/python -r requirements-dev.txt pytest-cov
   ```
6. **Tailscale** installed and logged in on the workstation (needed to reach `jarvis:8781`/`:8782`
   once the box is up, and for the ACL precondition in section 2).
7. **AWS CLI profiles.** Four named profiles, set up as this runbook reaches the step that needs
   them:
   - `sparko` -- the existing management-account access (IAM user `Administrator`, account
     `080109295043`, organization `o-lcwqey40mr`, Identity Center in `us-east-1`). Used only for
     `infra/org/`.
   - `jarvis-prod` -- IAM Identity Center, `JarvisAdmin` permission set, account `jarvis-prod`.
     Used only for `terraform apply` in `bootstrap/` and `envs/prod`.
   - `jarvis-operator` -- IAM Identity Center, `JarvisOperator` permission set. Used for every
     `make release/deploy/status/restart/secrets-sync/sync-agents/oauth-login` target.
   - `jarvis-client` -- IAM Identity Center, `JarvisClient` permission set, a dedicated Identity
     Center user (not the human's own). Used only by the Windows tray client.

   Create each with:
   ```
   aws configure sso --profile <name>
   ```
   and refresh a session later with:
   ```
   aws sso login --profile <name>
   ```
   **Recommendation (PLAN.md 3.2, DESIGN.md 10.7):** `sparko` is a long-lived IAM user access key
   that can assume `OrganizationAccountAccessRole` into `jarvis-prod` -- SCPs do not bind the
   management account. Move management-account access to Identity Center (and deactivate that
   key) before the first apply if you are able to; this is your call, not something this repo
   enforces.

## 2. First-time order

Nothing here runs `terraform apply`. Every `plan`/`plan-org`/`plan-bootstrap` target stops at
`plan.out`/`plan.txt` for review; you apply by hand with
`AWS_PROFILE=<profile> terraform -chdir=infra/<root> apply plan.out`.

### 2.1 `org/` (management account, profile `sparko`)

1. Create a state bucket for `org/` in the **management account** yourself (`org/` does not
   create it; `infra/org/backend.hcl.example` names it `MANAGEMENT_STATE_BUCKET`). Copy the
   example and fill it in:
   ```
   cp infra/org/backend.hcl.example infra/org/backend.hcl
   ```
   Edit `bucket` in `infra/org/backend.hcl` to that bucket's name.
2. Create `infra/org/org.auto.tfvars` (gitignored, no example file ships -- there are no secret
   values in it, just this account's identifiers):
   ```
   account_email            = "<root email for the new jarvis-prod account>"
   attach_region_scp        = false
   attach_guardrail_scp     = false
   create_permission_sets   = false
   ```
3. Plan and review:
   ```
   make plan-org
   ```
   Expected: `Review infra/org/plan.txt. The human applies; this target never does.` Read
   `infra/org/plan.txt`. It creates the `jarvis-prod` account and attaches only
   `jarvis-org-guard` (always on; the region-deny and guardrail SCPs default off, AD27/AD28).
4. Apply by hand:
   ```
   AWS_PROFILE=sparko terraform -chdir=infra/org apply plan.out
   ```
5. Record the new account id:
   ```
   AWS_PROFILE=sparko terraform -chdir=infra/org output -raw account_id
   ```

**Region-deny SCP warning (DESIGN.md 11.1, AD28).** Region-deny SCPs have broken real AWS
services before: an SCP evaluates the region of the *request*, not the resource, so ACM for
CloudFront, Route 53 DNSSEC keys in KMS, the STS global endpoint, and the Billing/Budgets/
Support/Health consoles (all us-east-1-only) can fail with a misleading `AccessDenied`. Do not
set `attach_region_scp = true` without this dry run:
```
AWS_PROFILE=sparko terraform -chdir=infra/org output -raw region_deny_policy_json > region-deny.json
```
```
aws accessanalyzer validate-policy --policy-type SERVICE_CONTROL_POLICY \
  --policy-document file://region-deny.json --region us-east-1
```
Expected: no `ERROR` or `SECURITY_WARNING` findings. Both commands are read-only and create
nothing. Then set `attach_region_scp = true`, `make plan-org`, review, and apply in a planned
window; smoke-test with `make plan`, `make status`, and a client token fetch afterward. Rollback
is setting the flag back to `false` and applying again.

An organization CloudTrail trail (`management-events`, us-east-1, multi-region) already exists
and already records `jarvis-prod` -- `org/` creates no trail of its own; `jarvis-org-guard`'s
`DenyCloudTrailTampering` statement is defense in depth only.

### 2.2 IAM Identity Center permission sets

Either let Terraform create them, or create them by hand.

**Terraform (recommended once you have the Identity Center user ids):**
```
aws identitystore list-users --identity-store-id <store-id> --region us-east-1
```
(workstation tool call; get `<store-id>` from `aws sso-admin list-instances --region us-east-1`).
Add to `infra/org/org.auto.tfvars`:
```
create_permission_sets  = true
operator_principal_id   = "<your Identity Center user id>"
workstation_principal_id = "<the dedicated workstation user's Identity Center user id>"
```
Then repeat `make plan-org`, review, and apply.

**Manual alternative (console):** create three permission sets in the existing Identity Center
instance -- `JarvisClient` (session PT1H), `JarvisOperator` (session PT4H), `JarvisAdmin`
(session PT1H, attach the AWS managed `AdministratorAccess` policy). Paste `JarvisClient`'s and
`JarvisOperator`'s inline policy JSON from `infra/DESIGN-IAM.md` section 3.12 verbatim. Assign
your own user to `JarvisOperator` and `JarvisAdmin`; assign the dedicated workstation user to
`JarvisClient` only (never your own user -- DESIGN.md 10.3).

### 2.3 `bootstrap/` (state bucket, profile `jarvis-prod`)

```
cp infra/bootstrap/bootstrap.auto.tfvars.example infra/bootstrap/bootstrap.auto.tfvars
```
Edit `expected_account_id` to the `jarvis-prod` account id from 2.1. `bootstrap/` uses local
Terraform state (no `backend.hcl` here -- it creates the very bucket `envs/prod` will use).
```
make plan-bootstrap
```
Expected: `Review infra/bootstrap/plan.txt. The human applies; this target never does.` Apply:
```
AWS_PROFILE=jarvis-prod terraform -chdir=infra/bootstrap apply plan.out
```
Get the state bucket name for the next step:
```
AWS_PROFILE=jarvis-prod terraform -chdir=infra/bootstrap output -raw state_bucket
```

### 2.4 `envs/prod`, first apply (`instance_enabled = false`)

```
cp infra/envs/prod/backend.hcl.example infra/envs/prod/backend.hcl
```
Fill in `bucket` with the `state_bucket` value above. Leave `encrypt`/`kms_key_id` unset (the
bucket's own default SSE-KMS applies; setting them without the full key ARN gets the write
denied).
```
cp infra/envs/prod/prod.auto.tfvars.example infra/envs/prod/prod.auto.tfvars
```
Fill in `expected_account_id` (the `jarvis-prod` account id) and `alert_email` (a real address --
you must click its SNS confirmation link before any alarm reaches you). Leave
`instance_enabled = false` for this first apply.
```
make plan
```
Expected: `Review infra/envs/prod/plan.txt. The human applies; this target never does.` This plan
creates the VPC, KMS keys, the six empty secrets, the artifacts and state-adjacent buckets, the
IAM role, the SSM documents, and the Elastic IP and ENI -- but no EC2 instance yet
(`instance_enabled = false` gates only `aws_instance` and the disk/status alarms). Apply:
```
AWS_PROFILE=jarvis-prod terraform -chdir=infra/envs/prod apply plan.out
```

### 2.5 Tailscale ACL precondition (before the first boot, DESIGN.md 10.5/AD24)

A default-allow tailnet policy exposes every port on the instance, including 22, the moment it
joins. Apply the ACL **before** flipping `instance_enabled = true`.

1. Edit `infra/modules/tailscale_acl/policy.hujson`: replace every `REPLACE_ME@example.com` with
   the workstation user's Tailscale login, and confirm the group name
   (`group:jarvis-workstation`) matches what you want in your tailnet.
2. Either apply it with Terraform (`manage_tailscale_acl = true` in `prod.auto.tfvars`, needs
   `TAILSCALE_API_KEY`, then `make plan` / apply as above), or paste the file's contents into the
   Tailscale admin console's ACL editor and save. Tailscale evaluates the file's own `tests` block
   on save and refuses to save if any test fails.
3. Verify from a **non-workstation** tailnet device (after the box has joined in section 2.6):
   `jarvis:8781`/`:8782` unreachable to anything but the workstation group, and `jarvis:22`
   unreachable to everyone.

### 2.6 Second apply (`instance_enabled = true`)

Do this only after section 3 (secrets populated). Edit `infra/envs/prod/prod.auto.tfvars`:
```
instance_enabled = true
```
```
make plan
```
Review `infra/envs/prod/plan.txt` (it should show only the instance, launch template, and the two
alarms being added). Apply:
```
AWS_PROFILE=jarvis-prod terraform -chdir=infra/envs/prod apply plan.out
```
The instance boots, cloud-init runs `bootstrap.sh`, and `post-boot-assert` runs at the end of
bootstrap. Confirm the SNS email subscription by clicking the link AWS just sent to
`alert_email` -- no alarm reaches you until you do.

## 3. Populate secrets

Terraform creates six secrets, empty. **Populate four; the instance writes the other two
itself** (DESIGN.md C14):

| Secret | You populate? | Shape |
|---|---|---|
| `jarvis/work` | Yes | `{"SLACK_BOT_TOKEN":"...","SLACK_APP_TOKEN":"...","SLACK_OWNER_USER_ID":"...","SLACK_MCP_URL":"...","SLACK_WORK_TOKEN":"...","GMAIL_MCP_URL":"...","GMAIL_WORK_TOKEN":"...","JEV_API_URL":"...","JEV_API_KEY":"..."}` (optional `"ANTHROPIC_API_KEY"`) |
| `jarvis/personal` | Yes | `{"TELEGRAM_BOT_TOKEN":"...","TELEGRAM_OWNER_CHAT_ID":"...","GMAIL_MCP_URL":"...","GMAIL_PERSONAL_TOKEN":"...","OFW_MCP_URL":"...","OFW_MCP_TOKEN":"...","GMAIL_WATCH_CLIENT_SECRETS":"<client secrets JSON as a string>","JEV_API_URL":"...","JEV_API_KEY":"..."}` (optional `"ANTHROPIC_API_KEY"`) |
| `jarvis/shared` | Yes (may be `{}`) | Any key here is merged into both modes; DESIGN.md 5 |
| `jarvis/tailscale` | Yes, once | `{"authkey": "tskey-auth-..."}` |
| `jarvis/work/api-token` | No | Plain 64-hex string, written by the instance (AD6) |
| `jarvis/personal/api-token` | No | Plain 64-hex string, written by the instance (AD6) |

A value that is identical in both `jarvis/work` and `jarvis/personal` (e.g. a shared
`GMAIL_MCP_URL`) fails `jarvis-secrets sync` unless its key lives in `jarvis/shared` instead --
`jarvis-secrets` then writes nothing and the units stay down (AD7).

Secret values never go in a heredoc, a here-string, argv, or a file in the repo directory (shell
history and stray files; DESIGN.md section 5). Each value travels clipboard -> stdin -> AWS CLI.
The JSON below shows the shape only; the values are placeholders.

`GMAIL_WATCH_CLIENT_SECRETS` must be single-line (minified) JSON inside its string:
`ops/aws/bin/jarvis-secrets` rejects any value that contains CR or LF.

1. Assemble the `jarvis/work` JSON in a password manager or editor outside the repo, then copy
   it to the clipboard. Shape (display only, do not paste this into a shell):
   ```
   {"SLACK_BOT_TOKEN":"<value>","SLACK_APP_TOKEN":"<value>","SLACK_OWNER_USER_ID":"<value>","SLACK_MCP_URL":"<value>","SLACK_WORK_TOKEN":"<value>","GMAIL_MCP_URL":"<value>","GMAIL_WORK_TOKEN":"<value>","JEV_API_URL":"<value>","JEV_API_KEY":"<value>"}
   ```
2. Upload it from the clipboard (macOS):
   ```
   pbpaste | AWS_PROFILE=jarvis-operator aws secretsmanager put-secret-value --secret-id jarvis/work --secret-string file:///dev/stdin --region us-east-2
   ```
   On Windows, run it from WSL (where `/dev/stdin` exists), with the Windows clipboard as the
   source. Native PowerShell is not supported for this step: the AWS CLI cannot read a blob
   parameter from stdin on Windows, and `(Get-Clipboard)` in argv would expose the value to
   other processes.
   ```
   powershell.exe -NoProfile -Command Get-Clipboard | tr -d '\r' | AWS_PROFILE=jarvis-operator aws secretsmanager put-secret-value --secret-id jarvis/work --secret-string file:///dev/stdin --region us-east-2
   ```
   Clear the Windows clipboard afterwards, from WSL:
   ```
   powershell.exe -NoProfile -Command "Set-Clipboard -Value ' '"
   ```
   Expected: a JSON response with `"Name": "jarvis/work"` and a new `"VersionId"`.
3. Clear the clipboard:
   ```
   pbcopy </dev/null
   ```
   Expected: no output.
4. Repeat steps 1 to 3 for `jarvis/personal` (shape in the table above) and `jarvis/shared` (`{}`
   is a valid value if you have nothing to share), changing `--secret-id` each time.
5. Create a one-off Tailscale auth key in the admin console (Settings > Keys > Generate auth
   key): **Reusable: off, Ephemeral: off, Pre-authorized: on, Tags: `tag:jarvis`, Expiry: 1 day**
   (`tag:jarvis` is owned by `autogroup:admin`, so you need admin rights on the tailnet). Copy
   the JSON to the clipboard. Shape (display only):
   ```
   {"authkey": "tskey-auth-<value>"}
   ```
6. Upload it from the clipboard:
   ```
   pbpaste | AWS_PROFILE=jarvis-operator aws secretsmanager put-secret-value --secret-id jarvis/tailscale --secret-string file:///dev/stdin --region us-east-2
   ```
   Expected: a JSON response with `"Name": "jarvis/tailscale"` and a new `"VersionId"`. Then clear
   the clipboard (`pbcopy </dev/null`).

   `jarvis/tailscale` is read only once, at first boot, by cloud-init; the value is written to a
   tmpfs file, consumed by `tailscale up`, and shredded (`ops/aws/lib/tailscale-join.sh`). You may
   empty this secret afterward:
   ```
   aws secretsmanager put-secret-value --secret-id jarvis/tailscale --secret-string '{}' --region us-east-2 --profile jarvis-operator
   ```
   Nothing on the box reads it again outside a manual key rotation (section 7 in
   RUNBOOK-ops.md).

Now do section 2.6 (flip `instance_enabled = true` and apply).

## 4. First release and deploy

```
git rev-parse --is-inside-work-tree
```
If that fails, `git init` first -- `make release` refuses a dirty or non-git tree.
```
make release
```
Expected: a `[release] released <sha> (jarvis-<sha>.tar.gz, jarvis-<sha>.tar.gz.sha256) ->
s3://jarvis-artifacts-<account-id>/releases/<sha>/` line, then, as the last line, the bare
40-character `<sha>` (`scripts/release.sh`).
```
make deploy SHA=<sha-or-prefix-from-above>
```
Expected: `resolved <sha> -> <full 40-char sha>`, then (from `jarvis-deploy.sh` on the box)
`[jarvis-deploy] deployed <sha>`, then `wrote s3://.../releases/DEPLOYED = <sha>
(ops/aws/lib/first-deploy.sh reads this at boot)`.
```
make status
```
Expected: `== unit states ==` with `jarvis@work.service: active`, `jarvis@personal.service:
active`, and the rest of the `jarvis-status` report (heartbeat, outbox counts, disk, Tailscale).

If a deploy ever fails partway, `jarvis-deploy.sh` rolls back automatically -- see
"deploy rolled back" in `infra/RUNBOOK-ops.md` section 12.

## 5. OAuth login per mode

Each mode signs in to Claude Code (and, for personal, the Gmail watcher) once, interactively,
through a forwarded SSM session.
```
make oauth-login MODE=work
```
This forwards ports `3118,8766` (override with `OAUTH_PORTS=...`), opens an SSM shell as
`jarvis-work` in `~/vault`, and prints:
```
1. Run: claude
2. Run: /mcp
3. Authorize each server listed (a browser opens for OAuth servers; ports forwarded: 3118,8766).
   If a server's browser redirect never arrives, try: claude mcp login <name> --no-browser
```
Run those steps in the shell that opens. Exit with Ctrl-D once every server in `/mcp` shows
authorized. The script then opens one more `AWS-StartInteractiveCommand` session (never
`AWS-RunShellScript`/`send-command` -- `JarvisOperator` grants neither) that sudo's to
`jarvis-<mode>`, asks `claude -p` which Gmail account it is connected to, and parses an appended
`__rc=<N>` exit-status marker out of the session output (the session's own exit code is always 0,
regardless of the remote command). Expected: `[oauth-login] connected Gmail account (work):
work@example.com`. This check is advisory -- a failed marker or a non-zero session exit logs
`verification failed (session exit <N>, marker '<marker>'); check manually with /mcp` but never
fails the whole `oauth-login` run (you already did the interactive part above).
```
make oauth-login MODE=personal
```
Same flow, plus a fourth printed step:
```
4. Also run (env vars first, so GMAIL_WATCH_CLIENT_SECRETS etc. are set -- read line by line,
   never "source"/". ", so a value containing shell metacharacters is neither mangled nor
   executed):
   while IFS='=' read -r k v; do [ -n "$k" ] && export "$k=$v"; done < ~/.jarvis/env
   /opt/jarvis/current/.venv/bin/python -m jarvis.gmail_watch --auth
```
Run that in the same shell after `/mcp` is done. It prints `Gmail watcher authorized for:
<email>` -- confirm that is the personal Gmail account before exiting.

## 6. Syncthing pairing

Each mode is a **separate** Syncthing instance (own device id, own GUI, own sync port). Pairing is
two one-time steps per mode: get the server's device id, add the workstation's device id to it.

1. Get each mode's server-side device id over an SSM shell (`ssm:StartSession` on
   `AWS-StartInteractiveCommand`, the same document `sync-agents.sh`/`oauth-login.sh` use). From a
   port-forward or a plain `aws ssm start-session --target <instance-id>` shell:
   ```
   sudo runuser -u jarvis-work -- syncthing cli show system | python3 -c 'import json,sys;print(json.load(sys.stdin)["myID"])'
   ```
   ```
   sudo runuser -u jarvis-personal -- syncthing cli show system | python3 -c 'import json,sys;print(json.load(sys.stdin)["myID"])'
   ```
   (`syncthing cli` reads `$HOME/.local/state/syncthing` by default -- exactly this repo's config
   path -- so no `--home` flag is needed when run as that user.)
2. On Windows, run (or re-run) `install.ps1` with both ids -- it registers the **workstation's**
   side of each folder over its own local Syncthing REST API:
   ```powershell
   .\install.ps1 -SyncthingWorkDeviceId <work-id> -SyncthingPersonalDeviceId <personal-id>
   ```
3. Add the **workstation's** device id to each server-side instance (the step `install.ps1`
   cannot do itself). Get the workstation's own device id locally first (same box the tray runs
   on, after step 2 has started Syncthing there):
   ```powershell
   syncthing cli show system
   ```
   (read `myID` from the JSON, or use the Syncthing GUI's own "Show ID" at
   `http://127.0.0.1:8384` on the workstation). Then, in the same SSM shell as step 1:
   ```
   sudo runuser -u jarvis-work -- syncthing cli config devices add --device-id <workstation-id> --name workstation
   ```
   ```
   sudo runuser -u jarvis-work -- syncthing cli config folders jarvis-work devices add --device-id <workstation-id>
   ```
   Repeat both for `jarvis-personal` / folder `jarvis-personal`. `jarvis-syncthing-render` forces
   `introducer=false` and `autoAcceptFolders=false` on every device on every unit start regardless
   of what the add command defaulted to, so nothing here can widen sync scope on its own (AD8,
   "small items").
4. Accept the two folders on Windows if Syncthing prompts (it should auto-connect since both
   sides already named each other and the folder id).
5. Verify: edit a file in either vault on the workstation (or on the box, as that mode user) and
   confirm it appears in Obsidian on the other side within about a minute.

Continue with rotations, snapshot restore, alarm drills, teardown, the acceptance checklist, and
troubleshooting in [`infra/RUNBOOK-ops.md`](RUNBOOK-ops.md).
