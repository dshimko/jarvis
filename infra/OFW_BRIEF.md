# Brief: Build the OFW MCP server for Jarvis personal mode

Paste this whole file as the first message of a new Claude Code session opened in the `jarvis/`
repo. It is the task brief for that session. Where it conflicts with `infra/BRIEF.md` or
`infra/PLAN.md` on safety, the stricter rule wins.

---

You are building `ofw-mcp`, the MCP server that Jarvis personal mode calls as server `ofw`
(`mcp/personal.mcp.json`; tool names in `config.yaml` under `modes.personal`). It wraps
OurFamilyWizard (OFW) with a Playwright client. OFW records are legal evidence in a custody
matter: correctness and zero content leakage matter more than speed.

## 0. Read first, in this order

1. `CLAUDE.md` (model dispatch, hard rules, common pitfalls). Follow model dispatch exactly.
2. `infra/PLAN.md` (fixed ADs; the OFW section you add in step 1.1 goes after the last AD).
3. `infra/BRIEF.md` sections 0, 1, 4, 5, 9, 10.
4. `README.md` (outbox, approval hashes, isolation), `jarvis/outbox.py`, `jarvis/modes.py`.
5. `vaults/personal/CLAUDE.md`, `vaults/personal/.claude/agents/coparent.md`,
   `vaults/personal/.claude/agents/reviewer.md`, `vaults/personal/.claude/commands/ofw-check.md`.
6. The source to port (read only; never edit that repo): the human's OFW Companion checkout at
   `/Users/mba/Documents/sparko/divorceCommunications` (GitHub `dshimko/divorcedCommunication`).
   Read its `CLAUDE.md`, `.claude/rules/{ofw-scraping-hygiene,attorney-privilege,pii-and-legal-records}.md`,
   `packages/shared/src/privilege/denylist.ts`, and all of `services/ofw-worker/`
   (`vendor/ofw_client/{client,selectors,exceptions}.py`, `src/*.py`, `tests/*.py`). Never read
   its `.env`, `.run/`, `debug/`, or the database. Section 2.8 says what to keep and what to fix.

## 1. Rules for this session

1. All rules in `CLAUDE.md` and `infra/BRIEF.md` section 0 apply: no `terraform apply`/`destroy`,
   no secret values in code/tfvars/state, no inbound rules, no IAM users or keys, ask before any
   AWS service not in the brief.
2. **No OFW content anywhere except the model's context and the personal vault.** Not in logs,
   exceptions, test fixtures, commit messages, PR bodies, screenshots, DOM dumps, temp files on
   disk, or your own replies to the human beyond what they ask to see. Metadata only: ids, counts,
   durations, status, `error_class`, selector *names*.
3. **Never send a real OFW message or create a real journal entry**, not even as a test. The
   first real send is a human-approved outbox item after the write gate (phase W).
4. Never read `~/.config/ofw-companion/secrets.json` or any other stored OFW credential. The
   human types credentials into the headed session or writes them to Secrets Manager.
5. A live OFW login happens only when the human is present and says go. Live tests are
   read-only and print counts only.
6. If a hook or permission prompt blocks a command, stop and report. Never route around it.
7. Commits only when the human asks, on a feature branch, no Claude attribution.

### 1.1 First actions (planner)

1. The human accepted every recommendation (2026-09-25); section 3 records the answers. Do not
   re-ask them. Ask only about something this brief does not cover.
2. Add a section "OFW MCP server" to `infra/PLAN.md` recording section 2 below as AD33 onward.
   From then on PLAN.md is the interface source of truth.
3. Write `.claude/agents/ofw-engineer.md` (Opus; tools Read, Write, Edit, Grep, Glob, Bash;
   scope: the `ofw-mcp` repo only). It registers only at the next session start, so in this
   session dispatch it as `subagent_type: general-purpose`, `model: opus`, first line "Read and
   follow `.claude/agents/ofw-engineer.md`".
4. Update `TODO.md` and `infra/PLAN.md` section 7 with the human-owned steps in section 6.

## 2. Fixed decisions (recommended defaults; the human may override in 1.1)

### 2.1 Hosting and transport
- Runs on the Jarvis EC2 instance as `ofw-mcp.service`, OS user **`jarvis-ofw`** (uid 2003,
  home 0700, nologin). This is a deviation from brief section 1 / AD17 (two users); record it.
- Streamable HTTP on **`127.0.0.1:8783`**, path `/mcp`. No TLS (loopback). iptables `OUTPUT`
  rule with `-m owner`: only root and `jarvis-personal` may connect to 127.0.0.1:8783; persist it
  and assert it in `post-boot-assert` (mirror the IMDS rule).
- `OFW_MCP_URL=http://127.0.0.1:8783/mcp` in `jarvis/personal`. Replace the vercel example in
  `env/personal.env.example`.
- Local profile (WSL/Mac dev): same unit shape, loopback bind, credentials from
  `~/.config/ofw-mcp/secrets.json` (0600, created by the human).
- Fallback if OFW blocks AWS IPs (decided after the phase D probe): run on the WSL box, reach it
  by MagicDNS over Tailscale, one ACL grant `tag:jarvis -> workstation tcp:8783` (deviation from
  AD24; applied by the human only if needed). No residential proxies.

### 2.2 Secrets and tokens
- New secret **`jarvis/ofw`**: `{"OFW_USERNAME","OFW_PASSWORD","OFW_MCP_TOKEN_SHA256",
  "OFW_MCP_WRITE_TOKEN_SHA256"}`, created empty by Terraform, synced by `jarvis-secrets` to
  `/home/jarvis-ofw/.jarvis/env` (0600, via `runuser`, per PLAN AD31). Instance-role
  `GetSecretValue` gains this one ARN; record as a documented deviation like G4.
- `jarvis/personal` gains `OFW_MCP_TOKEN` (read) and `OFW_MCP_WRITE_TOKEN` (executor only).
  Human generates both with `openssl rand -hex 32`.
- The server stores only sha256 of tokens and compares with `hmac.compare_digest`. The read
  token lists and serves read tools only; write tools are neither listed nor callable with it.
- `shared_violations` also checks `jarvis/ofw` against both modes.
- The outbox executor sends `OFW_MCP_WRITE_TOKEN` for `server: ofw`; `claude -p` sessions keep
  the read token from `mcp/personal.mcp.json`. The write token must never enter a `claude -p`
  subprocess env (add a test next to `tests/test_mode_isolation.py`).

### 2.3 Tool contract (names match `config.yaml`)
All times ISO 8601 with offset; server timezone `OFW_TZ` (IANA). Tool results mark OFW text as
untrusted third-party content.

Read (read token):
- `list_messages(since, folder="inbox"|"sent", limit<=50)` -> `[{id, thread_id, sent_at, sender,
  recipients, subject, has_attachments, privileged}]`. Never opens a message (no "viewed" receipt).
- `read_message(id)` -> list fields + `body`, `attachments: [{name, size}]`. Opening marks the
  message viewed in OFW; this is accepted. Privileged items return `body: null` until confirmed.
- `list_events(start, end)` -> `[{id, title, start, end, all_day, created_by, updated_at}]`.
- `list_expenses(since, status?)` -> `[{id, description, amount, currency, status,
  requested_by, due, updated_at}]`.
- `list_journal(since)` -> `[{id, created_at, title, body}]`.

Write (write token, and `OFW_WRITES_ENABLED=1`, default 0):
- `send_message(to: [alias], subject, body, reply_to_id?, client_ref)`. `to` accepts only
  aliases from server config (initially `coparent`) mapped to OFW recipient ids server-side.
- `create_journal_entry(title, body, date?, client_ref)`.
- `confirm_privileged(id)` releases one privileged body.
- `client_ref` = outbox id. A sent ledger (`client_ref`, `ofw_id`, timestamp; no content) at
  `~/.local/state/ofw-mcp/ledger.jsonl` refuses duplicates. After submit, verify in Sent and
  return `{status: "sent", ofw_id, sent_at}` or `{status: "sent_unverified"}`; never report
  `sent` without verification.
- No attachments in v1, read or write.

### 2.4 Session, limits, failures
- Playwright Chromium (bundled, arm64), one browser, one request at a time (asyncio lock),
  closed after 5 idle minutes. User agent derived from the bundled Chromium version, no macOS
  path lookup.
- `storage_state` at `/home/jarvis-ofw/.local/state/ofw-mcp/state.json` (0600). Log in only
  when the session is expired.
- Limits: 1 login per 10 min, 30 tool calls per hour, 25 page loads per call. Exceeded ->
  result `rate_limited`, not an exception.
- Circuit breaker: 2 consecutive login failures -> open; no login attempts until reset by
  `make ofw-reset` (SSM, root) or the Telegram control command `ofw reset`. Device/MFA
  challenge -> `OFWChallengeRequired`, breaker open.
- Missing required selector -> `OFWLayoutChanged` carrying the selector name only.
- Remove the vendored always-on `_dump_page` and `{exc}`-bearing messages. A DOM dump exists
  only behind `OFW_DEV_DUMP=1` in the local profile, writes to a tmpfs path, and the aws profile
  refuses to start with it set.
- Events (JSON logs, Jarvis `logsetup` filter reused, third-party loggers at WARNING):
  `ofw_call` (tool, duration_ms, status), `ofw_login` (status), `ofw_login_failed`
  (error_class), `ofw_breaker_open`, `ofw_layout_changed` (selector), `ofw_rate_limited`.
- CloudWatch: log group `/jarvis/ofw` (30 days, jarvis KMS key) via `jarvis-logexport@ofw`;
  metric filter + alarm `Jarvis/OfwLoginFailures` (Sum > 0, 1 period) and
  `Jarvis/OfwLayoutChanged`. Daemon makes no AWS calls (AD1).

### 2.5 Attorney privilege
- OFW does not allow third-party senders, so attorney material reaches OFW only when the other
  parent forwards or quotes it (Companion `attorney-privilege.md`, "OFW handling"). Detection is
  therefore on content, done server-side before anything is returned: an item is
  `privileged: true` when its subject, body, or any thread reply contains an address or bare
  domain from `PRIVILEGED_DOMAINS` (case-insensitive, whitespace-trimmed), or when a participant
  matches `PRIVILEGED_NAMES` (empty at first).
- `PRIVILEGED_DOMAINS = ("transitionslegal.com",)` is a code constant in `ofw_core/privilege.py`,
  not runtime config, mirroring Companion's `denylist.ts`. Same change discipline: only the
  human edits it, commit subject prefixed `privilege:`. No agent edits it.
- `list_messages` computes the flag from the list preview and returns `subject: null` for a
  flagged row. Privileged `read_message` withholds subject and body. `coparent` writes only "privileged item `<id>`
  awaiting confirmation" to `coparenting/open-items.md`.
- Telegram control command `privileged ok <id>` (handled in code, never the model) makes the
  daemon call `confirm_privileged(id)` with the write token; only that id is released.
- `reviewer` blocks any OFW draft that names a privileged party or says attorney/lawyer/counsel.

### 2.6 Jarvis-side changes
- PLAN AD10 changes: on each new notification id the watcher calls
  `brain.ask_detailed(mode, "/ofw-notify <internalDate - 15 min ISO>")`. It still reads only ids
  and `internalDate`; no subjects, no bodies. Nothing polls OFW on a timer.
- New vault command `vaults/personal/.claude/commands/ofw-notify.md`: coparent calls
  `list_messages`, `list_events`, `list_expenses` with that `since`, `read_message` for each new
  id, updates `coparenting/timeline.md` (with "opened by Jarvis at ..."), drafts a BIFF reply to
  `outbox/` only if a response is expected, then reviewer.
- Keep `/ofw-check` as the on-demand command ("personal, check ofw"). Remove the 12:00
  `ofw-check` schedule from `config.yaml` and update `tests/test_events.py` deliberately.
- Add the five `mcp__ofw__*` read tools to `coparent.md` `tools:`.
- Remove `ofw-mcp` from `modes.work.repos` (3.9).

### 2.7 Repo and packaging
- New private repo `dshimko/ofw-mcp`, Python 3.12, packages `ofw_core` (client) and `ofw_mcp`
  (server, official `mcp` SDK). `uv` for envs. Fixtures synthetic only.
- `make release` in Jarvis builds the `ofw-mcp` wheel at the sha in `deps/ofw-mcp.sha` and ships
  it in the tarball; `jarvis-deploy` installs it into its own venv under the release; rollback
  covers both. Bootstrap runs `playwright install --with-deps chromium` for `jarvis-ofw`.
  Binaries `jarvis-ofw` executes live in `/opt/jarvis/libexec` (CLAUDE.md pitfall).
- No sharing with ofw-companion or divorcedCommunication now.

### 2.8 Reuse map from OFW Companion `services/ofw-worker`

The Playwright client there is the human's own code: commits from 2026-04-30 to 2026-05-25. The
upstream `kherry/ofw-client` it names in `VENDOR.md` is Selenium and requests, not this code.
Port it into `ofw_core` directly, keeping git-blame context in the first commit message.

Works today and should be kept (proven against live OFW):
- Login: short username (not email), `SIGN_IN_URL`, the negative-lookahead `MESSAGES_URL_PATTERN`
  (the comment explains why a glob failed), the post-login bounce back to `/app/messages`.
- Inbox list: `a.messagePreview` rows, id from `id="messagePreview<n>"`, href
  `/app/messages/<folder>/<id>`, row sub-selectors, the react-window scroll loop with the idle
  cutoff.
- Detail: `#msgBody`, `#msgSender`, `#sentDate`, `#msgSubject` with the fallbacks; thread
  selectors under `#repliesContainer .previousMessage`.
- Launch flags, `playwright-stealth`, and the Chrome/Windows user agent that work today. Keep
  them as they are; add no further evasion.
- The 52 Python tests: port the ones that still apply.

Must change:
1. Delete `_dump_page` (always on; full HTML and PNG to `debug/`) and `_screenshot`. The dev
   dump is replaced by 2.4's `OFW_DEV_DUMP` rule.
2. Logs and exceptions carry `url`, `href`, and raw `exc` text. Replace them with `error_class`,
   selector names, and message ids.
3. `_parse_date` uses UTC. Parse in `OFW_TZ=America/Detroit`. Take the exact timestamp from the
   detail `#sentDate` so `since` works to the minute. List labels ("9:44 AM", "May 23") are
   used only for the scroll cutoff.
4. `incremental` has no cutoff. `list_messages(since)` stops scrolling at the first row older
   than `since`; confirm in phase S that the list is newest first.
5. Detail selectors defined but unused (`DETAIL_SENDER/SUBJECT/DATE`, thread) get wired in.
   `recipients = [sender]` and `has_attachments = False` are placeholders; replace them with
   real values found in phase S.
6. A new browser and login on every call: replace with 2.4's `storage_state` reuse.
7. Drop the FastAPI routes, the credential relay client, and the `.env` loader. Credentials come
   from the unit env (2.2).
8. Python 3.13 venv in Companion, 3.12 on the box: run the ported tests on 3.12.
9. `ua_strategy` looks up macOS Chrome. Use the bundled Chromium version in the same
   Windows UA template.

Not there, built new: sent folder, `read_message(id)` by URL, events, expenses, journal,
compose and send, journal create, privilege flagging in OFW content, session reuse, limits,
breaker, MCP server, auth.

Findings in Companion that are out of scope here: report them in the final report, do not fix
them.
- Its `POST /send` is a stub that returns `ok: true`, so the dispatch loop marks approved OFW
  drafts as sent although nothing was sent.
- `_dump_page` writes OFW content to `debug/` on every scrape.
- `_parse_date` returns UTC.

Running Companion (`make backfill`, `make scrape-test`) at the same time as ofw-mcp logs the
account in twice. The runbook says: use only one of them at a time.

## 3. Decisions recorded (human accepted all recommendations, 2026-09-25)

| # | Item | Answer |
|---|---|---|
| 3.1 | OFW terms-of-service risk (possible suspension of a court-ordered channel) | Accepted, with the limits in 2.4. Companion's `docs/runbooks/tos-acknowledgement.md` records the same acknowledgement |
| 3.2 | Licence of the reused client | Resolved: the Playwright client is the human's own code (2.8); port it |
| 3.3 | OFW Companion keeps running | Yes, on the workstation, never at the same time as ofw-mcp (2.8) |
| 3.4 | MFA / new-device verification | Unknown; phase S finds out. `OFWChallengeRequired` is built either way |
| 3.5 | `OFW_TZ` | `America/Detroit` (from Companion) |
| 3.6 | Privilege detection | Content-based on `PRIVILEGED_DOMAINS` (2.5); `PRIVILEGED_NAMES` starts empty, so no names are needed |
| 3.7 | New user `jarvis-ofw` (uid 2003) and secret `jarvis/ofw` | Approved; record as a deviation from brief sections 1 and 2 |
| 3.8 | Tailscale ACL grant for the WSL fallback | Approved, applied only if phase D picks the fallback |
| 3.9 | `ofw-mcp` under work-mode `repos` | Remove it |
| 3.10 | Local checkout path | `~/code/ofw-mcp` (matches `config.yaml`) |
| 3.11 | GitHub repo `dshimko/ofw-mcp` (private) | Human creates it; phase R work before that stays local |

## 4. Phases, owners, gates

Every phase ends with a `security-reviewer` (Opus) gate. The `ofw-mcp` phases also get
`ecc:python-reviewer` with `model: opus`. HIGH and CRITICAL are fixed by the owner before the
phase closes; MEDIUM and LOW that stay open go to `TODO.md` with a file ref and fix direction;
durable pitfalls go to `CLAUDE.md` Common pitfalls. Independent phases run in parallel.

| Phase | Owner (model) | Deliverable | Needs |
|---|---|---|---|
| S Selector session | ofw-engineer (Opus) + human | Headed Chromium on the Mac, human logs in. Inbox list and detail are already known (2.8); confirm they still match, newest-first order, `#sentDate` format, and whether `/app/messages/<id>` opens without the folder. New: sent folder, detail recipients and attachments, calendar, expenses, journal, compose form, recipient ids, challenge behaviour. Scrub script (in memory: replace every text node and attribute value except `class`, `id`, `data-*`, href patterns) produces fixtures; human reviews each before commit | human present |
| R Read server | ofw-engineer (Opus) | `ofw_core` + `ofw_mcp` read tools, tokens, limits, breaker, privilege withholding, logging, local unit, `pytest` >= 80% | S (the ported inbox code can start before S) |
| A Jarvis app | app-engineer (Opus) | 2.6, executor write token, `privileged ok` and `ofw reset` control commands, tests | none (mock the server) |
| I Infra | terraform-engineer (Opus): secret, IAM ARN, log group, filters, alarms; bootstrap-engineer (Sonnet): user, unit, iptables owner rule, Playwright install, secrets sync, post-boot asserts | `make tf-check` green, `plan.out` only if the human asks | none |
| P Packaging | deploy-engineer (Sonnet) | wheel in release, deploy/rollback covers it, `make ofw-login` (Chromium `--remote-debugging-port` on loopback as `jarvis-ofw` + SSM port forward, drive via `chrome://inspect`), `make ofw-reset` | R, I |
| D Deploy probe | human applies and deploys; planner reads results | On the box as `jarvis-ofw`: login + `list_messages(limit=1)`, print count and status only. Decide EC2 vs WSL fallback | P, human apply, Companion stopped |
| W Write path | ofw-engineer (Opus) | `send_message`, `create_journal_entry`, `confirm_privileged`, alias map, ledger, Sent verification; fixture and mock tests only. Dedicated adversarial gate on idempotency, token separation, privilege, and approval binding | R; one week of read-only use after D |
| Doc | docs-writer (Sonnet) | `infra/RUNBOOK.md` OFW section: secrets, `ofw-login`, challenge, breaker reset, fallback, manual OFW fallback if the account is suspended, enabling writes | all |

Order: 1.1, then S, A, and I in parallel; R after S; P after R and I; stop for D; W after the
human reports a week of read-only use; Doc last. Stop at every row that needs the human and say
exactly what you need.

## 5. Tests

- `ofw-mcp` (CI on GitHub Actions and locally): fixture parsing with headless Chromium via
  `page.set_content`; auth (read token cannot list or call write tools; bad token 401;
  `compare_digest`); privilege withholding and single-id release; rate limits; breaker opens
  after 2 failures and stays open; `OFWLayoutChanged` carries no content; idempotent `client_ref`;
  `sent_unverified` path; log filter on every event (a canary string in fixture text must never
  appear in captured logs or exception text); aws profile refuses `OFW_DEV_DUMP=1`.
- Jarvis: watcher calls `/ofw-notify` with the right `since` and still never fetches bodies;
  schedule removed; executor uses the write token only for `server: ofw`; write token never in a
  `claude -p` env; `privileged ok` and `ofw reset` are code-handled and refuse bad ids. The full
  existing suite stays green (`.venv/bin/pytest -q`).
- Live (`@pytest.mark.live`, `OFW_LIVE=1`, human present): login, one list per read tool,
  counts only. No live write tests.
- Post-boot asserts: `jarvis-work` cannot connect to 127.0.0.1:8783; `jarvis-ofw` cannot read
  `/home/jarvis-personal` and vice versa; `jarvis-ofw` cannot reach IMDS.

## 6. Human-owned steps (put these in PLAN.md section 7 and TODO.md)

1. Create the private repo `dshimko/ofw-mcp`.
2. Stop OFW Companion's OFW access (no `make backfill` or `make scrape-test`) during phases S and D and whenever ofw-mcp is live.
3. Attend phase S and log in to OFW.
4. After infra apply: populate `jarvis/ofw` and add `OFW_MCP_TOKEN`, `OFW_MCP_WRITE_TOKEN`,
   `OFW_MCP_URL` to `jarvis/personal`.
5. `make ofw-login`, clear any challenge; approve the phase D result or the fallback.
6. Fallback only: apply the ACL grant, put credentials on the WSL box.
7. After a week of read-only use: approve phase W, then set `OFW_WRITES_ENABLED=1` and approve
   the first real send through the outbox.

## 7. Definition of done

1. A real OFW notification email produces, with no timer involved, a timeline update and (when a
   reply is expected) a Telegram push for a BIFF draft.
2. `approve <id> <code>` sends it once through the write token, verified in Sent; a replay of the
   same outbox id is refused.
3. An OFW message quoting a `transitionslegal.com` address shows only metadata until `privileged ok <id>`.
4. Two bad passwords open the breaker, alarm, and push a Telegram notice; no third login occurs.
5. `grep` of `/jarvis/ofw` and `/jarvis/personal` log groups for a known message's text finds
   nothing.
6. All gates closed with no open HIGH or CRITICAL.

## 8. Final report

Phases done, gate results by severity, deviations recorded in PLAN.md, open `TODO.md` items,
the Companion findings from 2.8, and the exact next human step.

Also record in `TODO.md` (out of scope here): Jarvis personal mode's Gmail read tools can read
`transitionslegal.com` mail, which Companion blocks at ingestion. A later change should apply
the same deny-list to the personal Gmail path.
