# TODO: cross-phase backlog

Findings left unfixed after a review gate (MEDIUM and below), with a file reference and a
one-line fix direction. Remove an entry when it is done.

## From the PR integration review (2026-09-26)

- [ ] LOW `scripts/ssm-run.sh`: also stop polling on the other terminal SSM statuses
  (Undeliverable, Terminated, AccessDenied, InvalidPlatform); today they poll until the timeout
  and still exit 1 (fails closed). Direction: extend the terminal-state list.
- [ ] LOW `scripts/ssm-run.sh`: a persistently failing `get-command-invocation` (stale cached
  instance id) is mapped to Pending and polls until the timeout. Direction: count consecutive
  API errors and fail after a few; ties into the `.make/instance_id` entry below.
- [ ] LOW `scripts/oauth-login.sh`: `OAUTH_PORTS=","` yields an empty `ports` array that trips
  `set -u` on bash 3.2. Direction: validate the override and use the empty-array idiom.
- [ ] LOW `jarvis/modes.py` `_load_env` and `jarvis/secrets_check.py` `_read_env`: read env
  files with `dotenv_values(path, interpolate=False)` so a value containing `${...}` survives;
  then drop the `${` rejection in `ops/aws/bin/jarvis-secrets` `validate_keys`. Direction: change
  both readers together with a round-trip test.
- [ ] LOW `jarvis/api.py` local profile: both WSL daemons share `~/.jarvis/api_token`; the loser
  of the `O_EXCL` race at a simultaneous first start can read an empty file and exit (Restart
  recovers after 5 s). Not reachable on AWS. Direction: brief retry-read when the file is fresh.
- [ ] VERIFY on first boot: the `claude` native binary under `ProcSubset=pid`,
  `ProtectProc=invisible`, and an empty `CapabilityBoundingSet` (no `/proc/meminfo`, `/proc/stat`,
  `/proc/cpuinfo`). Acceptance: `make status` plus one voice utterance per mode after the first
  deploy. If it fails, relax `ProcSubset` first.

## From the re-targeting gate (us-east-1, OU, local state; 2026-09-25)

- [ ] MEDIUM `infra/org/README.md` and `infra/RUNBOOK-ops.md` section 10: a closed account stays
  in its OU as SUSPENDED for 90 days, so `terraform destroy` closes the account and then fails to
  delete the OU. Direction: document a second destroy after the account leaves the OU, and state
  that closure is permanent after 90 days and the email cannot be reused.
- [ ] MEDIUM `infra/tests/*.tftest.hcl`, `infra/tests/modules/policy_check/variables.tf`: the
  expected region is a fixed default, so a hardcoded region in a module would still pass.
  Direction: add one run with `aws_region = "eu-west-1"` passed through to `policy_check`.
- [ ] MEDIUM `ops/aws/bin/jarvis-secrets` `region()`: no unit test for env-wins, file fallback,
  and both-empty-raises. Direction: tests in `tests/test_ops_aws_tools.py` with an overridable
  region file path.
- [ ] MEDIUM `windows_client/jarvis_client/__main__.py`: no test that `cfg.aws_region` reaches
  both token sources. Direction: extend the token-source test with a non-default region.
- [ ] LOW `windows_client/install.ps1`: `aws_region` written unquoted; `config.py` does not
  validate it (empty value becomes `None` in argv). Direction: `Format-YamlScalar` plus a
  non-empty-string fallback to the default.
- [ ] LOW `windows_client/install.ps1` `.PARAMETER AwsRegion` help text claims the script calls
  AWS; it only writes `client.yaml`. Direction: reword.
- [ ] LOW `ops/aws/ssm/jarvis-deploy.sh:38` still reads `JARVIS_REGION` from `instance.env`
  before the region file, while `jarvis-secrets` reads only the file. Direction: one source.

## From Gate 5 (docs review, 2026-09-25)

- [ ] LOW `infra/org/policies.tf` JarvisOperator: add read-only `logs:FilterLogEvents` and `logs:GetLogEvents` on the `/jarvis/*` log groups so the operator can run the log checks in the runbook without the admin profile.

## From Gate 4 (deploy tooling review, 2026-09-25)

- [ ] LOW `Makefile` deploy section, `$(INSTANCE_ID_FILE)`: `.make/instance_id` is cached on first
  resolution and never invalidated, so replacing the instance (new launch template version,
  recovery from a snapshot) leaves stale commands targeting a dead instance id until `make clean`
  is run by hand. Direction: either drop the cache (re-resolve every time; one extra
  `ec2 describe-instances` call per command) or have `scripts/ssm-run.sh` detect a
  `TargetNotConnected`/invalid-instance error from `send-command` and prompt to re-resolve.
- [ ] LOW `windows_client/jarvis_client/api.py` `JarvisApi.iter_events`: a 200 response whose
  stream closes cleanly (server-side restart, LB idle timeout) falls through the `with` block
  without exception and loops back to `while True` immediately, with no backoff before
  reconnecting -- a flapping server could reconnect-storm. Direction: treat a clean stream close
  the same as the `except` branch (apply the backoff) unless it was via `exiting`/client shutdown.
- [ ] LOW `ops/aws/ssm/jarvis-deploy.sh` `ensure_build_user()`: duplicates
  `ops/aws/lib/users.sh`'s `jarvis-build` creation (bootstrap-engineer's file, not edited here).
  Direction: bootstrap owner considers having `install-release.sh` (or a shared `lib/users.sh`
  function) own this so there is exactly one place that creates `jarvis-build`.
- [ ] LOW `windows_client/install.ps1` is ~660 lines, over the project's usual file-size
  guidance. Direction: split into `install.ps1` (orchestration/Main) plus
  `lib/Install-Steps.ps1` (whisper/piper/tray/awscli/tailscale/syncthing functions) and
  `lib/Syncthing-Rest.ps1` (the REST API helpers), dot-sourced from the main script.

## From Gate 3b (app split review, 2026-09-25)

- [ ] LOW `jarvis/outbox.py` conflict gate: the `<id>*.sync-conflict-*` glob over-blocks on id
  prefixes (id `a` is blocked by `ab.sync-conflict-...`). Fails closed, documented in the tests.
  Direction: match `<id>.sync-conflict-*` and `<id>.*.sync-conflict-*` only.
- [ ] LOW `jarvis/logsetup.py`, `jarvis/outbox.py`, `jarvis/brain.py`: the module-global mutable
  `_REDACTOR` list pattern is repeated three times. Direction: one holder in `jarvis/redact.py`.
- [ ] LOW `jarvis/logsetup.py` ContentFilter mutates the LogRecord in place; fine with one
  handler. Direction: build a copy if a second handler is ever added.
- [ ] LOW `jarvis/gmail_watch.py` marks ids seen before `/ofw-notify` runs, so a failed notify is
  never retried for those ids. The 12:00 `ofw-check` backstop no longer exists by design (AD39:
  nothing polls OFW on a timer); a later notify covers the missed items only when they fall inside
  its 15-minute lookback, otherwise the human runs `personal, check ofw` (`/ofw-check`).
  Direction: a bounded retry (for example keep the earliest failed `since` and reuse it on the next
  poll, at most N times), and document the on-demand check in the runbook.
- [ ] LOW `ops/jarvis@.service` (WSL): when the `ExecStartPre` secrets check fails,
  `Restart=always` retries every 5 s and the unit flaps in the journal (key names only, no leak).
  Direction: `RestartPreventExitStatus=1` on the unit, or a runbook note.

## From Gate 2 (Terraform review, 2026-09-25)

- [ ] VERIFY on first boot: `token_publish_failed` must not appear in `/jarvis/<mode>` logs. If
  it does, the KMS grant for the token secrets is still too narrow. Direction: runbook acceptance
  step.

## From Gate 1 (design review, 2026-09-25)

- [ ] LOW `infra/DESIGN-IAM.md` W6 (`AlarmsAndBudgetsToSns` key statement): the
  `aws:SourceAccount` condition is documented for Budgets but not confirmed for CloudWatch
  alarms publishing to the KMS-encrypted SNS topic. The runbook's alarm drill (stop `jarvis@work`,
  expect the heartbeat email) is the acceptance test. If no email arrives, drop the condition for
  `cloudwatch.amazonaws.com` only.

## OFW MCP server: human-owned steps (2026-09-25; `infra/PLAN.md` 7.1)

- [ ] Create the private repo `dshimko/ofw-mcp`; until then the code stays uncommitted in `~/code/ofw-mcp`.
- [ ] Stop OFW Companion's OFW access (no `make backfill`, no `make scrape-test`) during phases S and D and whenever ofw-mcp is live.
- [ ] Attend phase S: run `scripts/selector_session.py` on the Mac, log in to OFW, review each scrubbed fixture before it is committed.
- [ ] After the infra apply: populate `jarvis/ofw` (`OFW_USERNAME`, `OFW_PASSWORD`, `OFW_MCP_TOKEN_SHA256`, `OFW_MCP_WRITE_TOKEN_SHA256`, `OFW_RECIPIENTS`) and add `OFW_MCP_TOKEN`, `OFW_MCP_WRITE_TOKEN`, `OFW_MCP_URL` to `jarvis/personal`.
- [ ] `make ofw-login`, clear any device or MFA challenge; approve the phase D result or choose the WSL fallback.
- [ ] Fallback only: apply the Tailscale ACL grant (`tag:jarvis -> workstation tcp:8783`), put the credentials on the WSL box.
- [ ] After a week of read-only use: approve phase W, then set `OFW_WRITES_ENABLED=1` and approve the first real send through the outbox.

## Out of scope for the OFW MCP work (recorded 2026-09-25)

- [ ] MEDIUM personal-mode Gmail read tools (`mcp__gmail__search`, `mcp__gmail__read_message` in `config.yaml`; tool policy in `jarvis/brain.py`): personal mode can read `transitionslegal.com` mail, which OFW Companion blocks at ingestion (`packages/shared/src/privilege/denylist.ts`). Direction: apply the same deny-list on the personal Gmail path (a code-side filter in front of the Gmail MCP results, or a Gmail label-and-skip like Companion) so privileged mail never enters a `claude -p` context without confirmation.

## From the OFW phase I gate, Terraform half (2026-09-25)

- [ ] MEDIUM `infra/DESIGN.md` (inventory, 4.2 rows P2 and P8, sections 5, 7.2, 7.3, 8.2) still describes six secrets, three log groups, and four SSM documents. Direction: docs-writer updates it to PLAN.md 3.3 (seven secrets, `/jarvis/ofw`, three ofw filters and alarms, `jarvis-ofw-login` and `jarvis-ofw-reset`) in the Doc phase.
- [ ] LOW `infra/modules/observability/alarms.tf` `jarvis-heartbeat-ofw` (`missing = breaching`) sits in ALARM from the phase I apply until ofw-mcp is deployed, and permanently if the AD33 WSL fallback is chosen. Direction: RUNBOOK note; the phase D decision removes or retargets the alarm under the fallback.

## From the OFW phase I gate, bootstrap half (2026-09-25)

- [ ] LOW `ops/aws/bin/jarvis-secrets` `secret_invalid_keys` (pre-existing) prints malformed key names verbatim; a value pasted as a key would be printed. Direction: print a count or index instead of the name.
- [ ] LOW `ops/aws/post-boot-assert.sh`: the work/ofw cross-home directions and the personal-to-ofw process-visibility direction are not asserted. Direction: extend the two loops to every ordered pair of the three users.

## From the ofw-mcp phase R scaffold gates (2026-09-25)

- [ ] LOW ofw-mcp `limits.py`: a corrupt `logins.json` or `calls.json` fails closed (`rate_limited` until a human fixes the file) with only an ERROR `ofw_state_unreadable` log line. Direction: runbook line in the OFW section (Doc phase) and, if it ever fires, a metric filter and alarm on `/jarvis/ofw` for that event.
- [ ] LOW `vaults/personal/.claude/commands/ofw-notify.md`: `list_messages` now returns `complete: false` when the scroll stopped on idle rather than on `since` or `limit`. Direction: the command tells coparent to retry once with a smaller `limit` and otherwise note "possibly incomplete" in the timeline, never to loop (each call costs a `CALLS_PER_HOUR` slot).
- [ ] MEDIUM ofw-mcp `ofw_core/detail.py` `read_detail`: the landing check is URL-only and time-bounded (every URL seen until 500 ms after parsing); a router that renders another message before changing the URL, or rewrites later than that, is not caught. Phase S must find a DOM attribute carrying the message id so the check binds to the DOM and drops the timing dependence (`docs/SELECTORS.md` asks for it).

## From the OFW phase P gate (2026-09-26)

- [ ] LOW `ops/aws/ssm/jarvis-deploy.sh`: each release's `playwright install chromium` as `jarvis-ofw` leaves older Chromium builds in `/home/jarvis-ofw/.cache/ms-playwright` when the pinned Playwright version changes. Direction: runbook note plus a `runuser -u jarvis-ofw -- playwright uninstall --all` step before the install, or a periodic prune, once the box has been through two Playwright bumps.
- [ ] LOW `Makefile` `deploy`: `SSM_DEPLOY_TIMEOUT_S ?= 1800` equals the `jarvis-deploy` document `timeoutSeconds`, so a deploy that runs close to the limit is reported as failed by the client while the box may still finish (fails safe: `DEPLOYED` is not written). Direction: document timeout plus 60.
- [ ] LOW `Makefile` `ofw-login`: `$$(($(OFW_WAIT_SECONDS) + 120))` reads a leading-zero value as octal and aborts. Direction: `10#` prefix or validate the value in the recipe.

## From the merge of `feat/aws-migration` into `ofw-mcp` (2026-09-26)

- [ ] LOW `ops/aws/bin/jarvis-secrets` `validate_keys`: the `'` and `${` rejections exist for the single-quoted python-dotenv mode files, but also apply to `jarvis/ofw`, which is written raw for ofw-mcp's `read_raw_env`; an OFW password containing `'` is refused. Direction: a raw-target validator that rejects only CR and LF for `ofw`.
- [ ] MEDIUM `tests/test_ops_aws_tools.py`: the ofw target's value handling is confirmed only by reading the code. Add a test that (1) a value containing `#`, `=`, and spaces reaches the `jarvis-ofw` env body unquoted and unchanged, and (2) a value containing `'` or `${` returns 1 with only the key name in the output; mutation-test it.

## From the companion-consumer gate (2026-09-27)

- [ ] LOW `ops/aws/post-boot-assert.sh`: no live check proves `jarvis-work` is rejected when it connects to the box's own Tailscale IP on 8783 (and 8781/8782 for the other mode); the `-o lo` owner triples cover it and `iptables -C` verifies them, so it is a missing test, not a hole. Direction: `runuser -u jarvis-work -- curl http://$ts_ip:8783/healthz`, expect exit 7.
- [ ] LOW `Makefile` `tf-validate`: `terraform init -backend=false` in `infra/envs/prod` reuses the cached S3 backend in `.terraform/` and fails once the SSO grant has expired. Direction: run validate with a scratch `TF_DATA_DIR` so `make tf-check` never needs credentials.
- [ ] LOW `ops/aws/post-boot-assert.sh` check 9 (pre-existing): the `jarvis@<mode> not active yet, bind check skipped` branch prints PASS on a skipped precondition. Direction: the same `SKIP-FAIL` counted line used by checks 4b and 12b.

## From the companion token gate (2026-09-27)

- [ ] LOW `infra/DESIGN.md` P8 (`JarvisOperator` "PutValueSecrets" table row): lists only the
  4 ARNs for `jarvis/{work,personal,shared,tailscale}`, not the later `jarvis/ofw` (AD34, seventh
  secret). Direction: add the fifth ARN to the P8 row (docs phase); confirm against the actual
  Terraform policy (`infra/org/policies.tf`) which ARNs `PutValueSecrets` grants today.
