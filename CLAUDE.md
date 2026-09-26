# Jarvis: instructions for Claude Code

Read `README.md` for what Jarvis is and the isolation guarantees, `PORTING.md` for the Windows
port history, and `infra/PLAN.md` for the AWS migration plan and its fixed decisions.

## Model dispatch (mandatory)

- **Fable plans only.** When the session model is Fable it writes plans, agent definitions, gate
  decisions, and the final report. It writes no application code, Terraform, scripts, or docs.
- **Opus builds the hard parts and reviews**: design (`infra-architect`), Terraform and IAM
  (`terraform-engineer`), core app changes (`app-engineer`), every gate (`security-reviewer`).
- **Sonnet builds the rest**: bootstrap scripts and units (`bootstrap-engineer`), deploy tooling
  and the Windows client (`deploy-engineer`), docs (`docs-writer`), workspace preparation.
- The agent definitions in `.claude/agents/` pin their model. When calling a plugin agent (for
  example `ecc:code-reviewer`), pass an explicit `model` override; never rely on `inherit`.
- Never use `subagent_type: fork` from a Fable session: forks run on the parent model.
- Files added to `.claude/agents/` register as agent types only at session start. In the same
  session, dispatch with `subagent_type: general-purpose`, the matching `model`, and a first line
  telling the agent to read and follow its role file.
- If a hook or permission prompt blocks a command, stop and report it. Never reach the same
  effect another way (a different tool, a scripting language, a copied binary). A blocked action
  is a question for the human, not an obstacle.
- Independent phases run in parallel; each phase ends with a `security-reviewer` gate. HIGH and
  CRITICAL findings are fixed by the owning builder before the phase closes; MEDIUM and LOW that
  stay open go to `TODO.md`; durable pitfalls go to this file.

## Hard rules for the AWS work (from `infra/BRIEF.md` section 0)

1. Never run `terraform apply` or `destroy`; produce `plan.out` and stop. The human applies.
2. No secret values in Terraform code, variables, tfvars, or state. Secrets are created empty.
3. No inbound security group rules, SSH keys, key pairs, IAM users, or access keys.
4. No personal-mode content (OFW text, email bodies, transcripts, utterances, replies, drafts)
   in any log. Metadata only: ids, event types, durations, status, error class.
5. Ask before adding an AWS service the brief does not list.

## Safety invariants (do not weaken; the tests enforce them)

Per-mode subprocess env with only that mode's secrets; repo-side MCP config with
`--strict-mcp-config`; path denies including the peer vault and `~/.claude`; write tools always
disallowed in vault sessions; approval binds body and read-back hashes; per-mode lock around
approve and execute; every API string redacted; API token 0600 created with `O_EXCL`; bearer auth
with `hmac.compare_digest`; no CORS; TrustedHost. On AWS add: one OS user per mode, root-only
IMDS, root-only secrets sync, Tailscale-only bind, Syncthing conflict gate.

## Commands

```bash
uv venv .venv --python 3.12 --seed && uv pip install --python .venv/bin/python -r requirements-dev.txt pytest-cov
.venv/bin/pytest -q                    # both suites (tests/ and windows_client/tests/)
make tf-check                          # fmt, validate, tflint, trivy, terraform test (after Phase 2)
make plan                              # terraform plan -out plan.out for envs/prod (needs AWS_PROFILE)
```

## Common pitfalls

- System Python on this Mac is 3.9; the code needs 3.12 and a venv. Do not run `pytest` outside
  `.venv`. The `python3.12` on PATH is uv-managed and its `-m venv` cannot bootstrap pip; create
  the venv with `uv venv` as shown above.
- `tflint` lives in `~/.local/bin` (no Homebrew formula exists any more); make sure that directory
  is on PATH before `make tf-check`.
- Homebrew `terraform` is pinned at 1.5.7 (licence change). The brief needs >= 1.10 from
  `hashicorp/tap/terraform`; unlink the old formula rather than uninstalling it.
- `timeout` does not exist on macOS; use `gtimeout` from coreutils or the tool's own timeout flag.
- The CloudWatch agent cannot read journald; logs reach it through `jarvis-logexport@` (PLAN AD2).
- The mode daemons have no AWS credentials by design (IMDS is root-only). Metrics come from log
  metric filters, never from the app calling CloudWatch (PLAN AD1).
- The ecc GateGuard hook asks for two facts before the first Bash call of a session; state them
  and retry rather than disabling the hook.
- Commit messages and PR bodies carry no Claude attribution (user setting overrides the harness).
- Every root tool that touches `/home/jarvis-<mode>` must drop to that user with `runuser`;
  a root tool following a user-controlled path is a cross-mode write primitive (PLAN AD31).
- Binaries a mode user executes must live in a directory that user can traverse
  (`/opt/jarvis/libexec`, 0755), never in the root-only `/opt/jarvis/bin`.
- `httpx` logs full request URLs at INFO, which includes the Telegram bot token. Third-party
  loggers stay at WARNING and the JSON formatter redacts `msg` (PLAN AD11).
- Identity Center role ARNs have no region path segment when the instance is in us-east-1. Use
  `sso.amazonaws.com/*AWSReservedSSO_<Set>_*` in `aws:PrincipalArn` patterns.
- `mock_provider "aws"` randomises `data.aws_iam_policy_document.*.json`; build policies with
  `jsonencode()` so `terraform test` can inspect them.
- S3 `aws:kms` without a key id uses the `aws/s3` key; a `StringNotEqualsIfExists` key-id deny
  does not catch it. Deny the header being present without a key id.
- With the region-deny SCP, every CLI caller must pass `--region` explicitly (AD42: `us-east-1`,
  read from `$AWS_REGION`/`$JARVIS_REGION` or `/etc/jarvis/region` on the instance -- never a
  hardcoded literal outside Terraform).
- An organization CloudTrail trail (`management-events`, us-east-1, multi-region) already
  covers `jarvis-prod`; do not create a member-account trail.
- In an IAM Deny, `...IfExists` operators evaluate true when the key is absent. To deny only a
  wrong value that is present, use `Null: false` plus `StringNotEquals`. Multipart `UploadPart`
  is authorised as `s3:PutObject` and carries no SSE headers.
- Bootstrap checks that assert on users must run after the users exist; the IMDS guard has a
  `--rules-only` mode for the pre-user step.
- With `hidepid=invisible`, root daemons whose capability set lacks `CAP_SYS_PTRACE`
  (`systemd-logind`, `polkitd`) need the `gid=` group through `SupplementaryGroups`.
- `requirements.txt` is the only dependency manifest. Packages installed by hand into `.venv`
  hide missing dependencies, and lazy imports plus mocked services keep tests green. Every
  optional runtime dependency gets an import smoke test (`tests/test_dependencies.py`).
- `httpx` never raises on 4xx/5xx unless `raise_for_status()` is called or the status is
  checked. A bare `except Exception` around `httpx.post` is not error handling.
- A daemon thread whose run loop can raise dies silently while the heartbeat stays green. Every
  run loop catches, logs `<name>_error` with `error_class`, and continues.
- Per-mode daemons cannot check a secret or MCP literal across modes (they hold one mode's
  secrets by design). Every cross-mode check lives in `jarvis/secrets_check.py` and the root
  `jarvis-secrets` tool.
- `%h` and `%u` in a system unit resolve to root's values even with `User=`; use `/home/%i`.
- `StartLimitIntervalSec=` belongs in `[Unit]`; in `[Service]` it is silently ignored. Packaged
  units (`syncthing@`) carry their own `StartLimitBurst`, which overrides "retry forever".
- Syncthing `config.xml` options are child elements, not attributes; parse the XML, never grep.
- `tailscale status --json` is indented; select with `jq`, never a compact-JSON grep. The same
  applies to any `json.dumps` output with default separators.
- With IMDSv2 required, a tokenless GET returns 401 with a non-empty body; a non-empty check is
  not a check.
- Root `install -d`, `mkdir -p`, or `chown` on any path under a mode home is an AD31 violation
  even in bootstrap, because bootstrap is re-run on live boxes.
- Units that call IMDS or AWS at boot need `Wants=` plus `After=network-online.target`;
  `netfilter-persistent` runs before the network.
- A post-boot assertion must fail when its precondition (user, file, command, rendered config)
  is missing; "skipped" must never print PASS.
- `ExecStartPost` hooks that talk to AWS use the `-+` prefix so a transient failure never fails
  the daemon.
- Source-regex guards in `terraform test` must also match `dynamic "<block>"` forms, and a
  runtime output check covers only the resources it names; pair it with a resource-address
  inventory assert.
- A policy scan must prove every module's `policies` output is merged into the tested outputs;
  checking the local's name alone is vacuous for unwired modules. Mutation-test the tests.
- `terraform test` mocks the archive provider, so tests never exercise `archive_file`. The
  tar.gz output zeroes mtimes but keeps mode bits, so a `chmod` changes the bootstrap sha and the
  S3 key.
- SSM expands `{{ ... }}` anywhere in a Command document body; `ops/aws/ssm/*.sh` must never
  contain `{{` (for example Go templates in `--format`).
- Mutation checks in a scratch copy use `plan` or `terraform test` only, never `apply`.
- Secrets Manager `PutSecretValue` on a CMK-encrypted secret needs `kms:Decrypt` as well as
  `kms:GenerateDataKey`.
- gpg auto-creates only a homedir whose path ends in `/.gnupg`; create any other `--homedir`
  first with `install -d -m 0700`.
- In a Terraform template, bash `${VAR//x/y}` must be written `$${...}`. Render the template
  with `terraform console` (`templatefile(...)`) to check the output; never `apply`, even in a
  scratch directory with no resources.
- `cmd | grep -q` under `pipefail` can fail on SIGPIPE when the writer keeps writing after the
  match; capture to a variable or use `grep >/dev/null`.
- Any test that runs an `ops/aws` script must override every root-path default
  (`JARVIS_DEPLOY_LOCK` and similar). The on-box suite runs as `jarvis-build` on Linux, where
  `flock` and `sha256sum` exist, so passing on macOS does not mean passing on the box.
- rsync `src/dir/` copies the directory's contents; omit the trailing slash to land the
  directory inside the destination.
- `sudo -i` runs the target user's passwd shell, and the mode users have `nologin`. Use
  `sudo -u <user> -H bash -lc` or `runuser`.
- AWS CLI shorthand syntax cannot carry single quotes; pass `--parameters file://` JSON.
- `set -e` is disabled inside a function called from an `if`, `&&`, or `||` condition. Check
  each step explicitly.
- Operator tooling must not depend on `terraform output`: the state bucket is admin-only. Find
  the instance with `ec2 describe-instances` filtered by `tag:app=jarvis`.
- The box runs two Syncthing instances, so there are two device ids and two ports.
- Long-running client threads must catch every exception a token source can raise.
- SSM Command document parameter constraints (`allowedPattern`, `allowedValues`) are the only
  barrier between an SSM caller and a root shell, because `{{ Param }}` is spliced into the
  script. Pin them in a `terraform test`, or a loosened pattern goes unnoticed.
- A Chromium started with `--remote-debugging-port` on 127.0.0.1 is reachable by every local
  uid. Any loopback debug or forwarded port needs an iptables owner rule like 8783's.
- `ss` greps for `0.0.0.0`, `*`, or `[::]` do not prove "loopback only": a listener on the
  Tailscale or VPC address passes. Assert the local address equals 127.0.0.1.
- A tcp-reset REJECT and a closed port both give `curl` exit 7. A reject check proves the rule
  only after a listener is confirmed; without one, print a counted failure, never PASS.
- `echo "$empty" | grep -v PATTERN` succeeds because echo emits one empty line; guard any
  "anything other than" test with `[ -n "$var" ]`.
- jq `.flag // empty` treats `false` as absent; test the type instead.
- A new secret target in `jarvis-secrets sync` gates every unit with `Requires=jarvis-secrets`;
  an optional secret (`jarvis/ofw`) must be skippable, or an empty secret takes both modes down.
- `install-release.sh` copies only `systemd/*.service` and `*.timer`; a new `*.service.d`
  drop-in needs an explicit install step on the deploy path.
- All `Condition*=` lines in a unit are ANDed, whatever their type; only `|`-prefixed
  triggering conditions are ORed among themselves (`systemd.unit(5)`). Do not take a
  builder's claim about unit semantics on trust; the reviewer reads the man page.
- A dict-of-directives comparison of a unit file misses additive keys (`BindPaths`,
  `ReadWritePaths`, `SupplementaryGroups`) repeated before the real line; compare ordered lines
  and reject unlisted directives.
- Python `\d` matches any Unicode digit and `$` matches before a trailing newline; validate ids
  and status words with `[0-9]` and `re.fullmatch`, and check status words against a frozenset.
- Never put code-handled control tools (`confirm_privileged`, `reset_breaker`) in
  `write_tools`: that makes them outbox-executable. Deny them in the session instead.
- An SPA can restore the last-viewed page after navigation; a scraper must verify the loaded
  page's id matches the request before attributing content to it.
- Env-file readers disagree: the mode daemons read with python-dotenv (quotes stripped,
  `${VAR}` expanded), while ofw-mcp's `read_raw_env` splits on the first `=` and keeps the value
  byte for byte. `jarvis-secrets` single-quotes the mode files and writes the ofw file raw
  (`write_env(..., raw=True)`); quoting the ofw file puts literal quotes into the password.
- In a worktree session the Bash guard refuses compound commands, recursive deletes, inline
  perl, and heredocs whose text mentions the version-control tool or its hosting site; use
  plain single commands and the Write or Edit tool for such content.
- A deploy step that runs `runuser -u <new user>` must come after `setup_users`: on a live box
  the user exists only once `install-release.sh` has run.
- The on-box ofw-mcp suite sees only what `release.sh` ships (`tests/`, `pyproject.toml`, the
  lock). A test that imports from `scripts/` or reads repo files needs the `repo` marker, which
  the deploy excludes. Simulate by copying just those files to a scratch dir and running pytest.
- Tests never write tracked or soon-to-be-tracked repo files (`deps/ofw-mcp.sha`); make the
  path overridable and point tests at `tmp_path`.
- The client-side poll bound for an SSM command must exceed the document's `timeoutSeconds`,
  or the client gives up before the terminal status arrives.
- On macOS `/bin/bash` 3.2, `"${arr[@]}"` on an empty array under `set -u` is an unbound-variable
  error. Keep at least one element (`--region`) in shared argument arrays.
- `close_on_deletion = true` without `prevent_destroy` turns any ForceNew change on
  `aws_organizations_account` (email) into an account closure; review `org/` plans for
  "must be replaced".
- A closed member account stays in its OU as SUSPENDED for 90 days, so the OU cannot be deleted
  in the same `destroy`.
- Terraform policy tests with a fixed expected region do not prove region derivation; add a
  non-default-region run.
- The release tarball is also the test root on the box. Any test that reads a repo file
  outside `ARCHIVE_PATHS` in `scripts/release.sh` breaks every deploy; `tests/test_release_tree.py`
  runs pytest collection against the git-archive tree to catch this.
- `aws ssm wait command-executed` is capped at 100 s (20 x 5 s) with no CLI override; poll
  `get-command-invocation` for anything long-running (deploy, ofw-login).
- `ops/aws/ssm/*.sh` reach the box only as SSM document bodies. Anything bootstrap must run
  locally from `ssm/` has to be installed explicitly by `install-units.sh`.
- python-dotenv is not a raw `KEY=value` reader: unquoted values lose ` #...`, and `${VAR}` is
  expanded regardless of quoting (no escape syntax). `jarvis-secrets` writes single-quoted values
  and rejects a value containing `'` or `${`; the reader-side fix (`interpolate=False`) is in
  `TODO.md`.
- `tests/test_release_tree.py` archives HEAD, not the index, and runs collection only. To verify
  a staged merge: `git write-tree`, `git archive <tree> -- config*.yaml $ARCHIVE_PATHS`, extract to
  scratch, run the full suite there.
- `python3 -m py_compile` on `ops/aws/bin/*` leaves an ignored `__pycache__` behind; harmless, but
  a reviewer's compile check is not a no-op on the tree.
