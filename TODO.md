# TODO: cross-phase backlog

Findings left unfixed after a review gate (MEDIUM and below), with a file reference and a
one-line fix direction. Remove an entry when it is done.

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
- [ ] LOW `jarvis/gmail_watch.py` marks ids seen before `/ofw-check` runs, so a failed check is
  never retried for those ids; the 12:00 `ofw-check` schedule is the backstop. Direction: document
  in the runbook; consider a bounded retry.
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
