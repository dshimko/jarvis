# Porting Jarvis to Windows (WSL2 backend, Windows UI)

Phase 1 output: audit + plan. Every item is a checkbox that a later phase closes.
Reference: the porting brief (sections 1-11). Where the brief and the old README
disagree on safety, the stricter rule wins; places where this plan is stricter than
the brief are marked **[stricter]**.

---

## A. macOS-specific calls (all must go)

| # | Where | What | Fix | Phase |
|---|---|---|---|---|
| [ ] A1 | `jarvis/voice.py` `speak()` | `afplay <wav>` to play Piper output | Local voice moves to `windows_client/`; delete `jarvis/voice.py` | 4 |
| [ ] A2 | `jarvis/voice.py` `speak()` | `say <text>` fallback | Windows SAPI via `pyttsx3` in client | 3/4 |
| [ ] A3 | `jarvis/voice.py` `listen_forever()` | `afplay /System/Library/Sounds/Tink.aiff` start beep | `winsound.MessageBeep()` in client | 3/4 |
| [ ] A4 | `jarvis/voice.py` | `pynput.GlobalHotKeys` + `sounddevice` mic in the daemon (needs a display + audio device; neither exists in headless WSL) | Hotkeys + mic move to client; daemon is headless | 2/3 |
| [ ] A5 | `install.sh` | `brew install whisper-cpp`, `pip piper-tts`, model download into `~/.jarvis/models` | Replace with `install_wsl.sh` (no STT/TTS) + `windows_client/install.ps1` (whisper.cpp Windows build, Piper Windows) | 2/3 |
| [ ] A6 | `install.sh` | writes `~/Library/LaunchAgents/ai.sparko.jarvis.plist` | systemd user unit `~/.config/systemd/user/jarvis.service` | 2 |
| [ ] A7 | `ops/ai.sparko.jarvis.plist` | launchd job, logs to `/tmp/jarvis.*.log` | Delete. Logs go to journald (`journalctl --user -u jarvis`) | 2/4 |
| [ ] A8 | `README.md` setup step 5 | `launchctl load ...` | README rewrite for Windows | 4 |
| [ ] A9 | `config.yaml` `voice:` block | `tts: piper \| say`, `~/.jarvis/models`, `~/.jarvis/voices`, hotkeys | Remove `voice:` from daemon config. Client config lives at `%LOCALAPPDATA%\Jarvis\client.yaml` | 2/3 |
| [ ] A10 | `requirements.txt` | `sounddevice soundfile numpy pynput` (audio/hotkey deps in the daemon) | Drop them from the daemon. Add `fastapi uvicorn`. Client gets its own `windows_client/requirements.txt` | 2 |
| [ ] A11 | `jarvis/modes.py` `PASSTHROUGH` | `TMPDIR` (set on macOS, usually unset on Linux) | Harmless, keep. See B8 for PATH | n/a |

## B. Path and platform assumptions

| # | Where | Assumption | Fix | Phase |
|---|---|---|---|---|
| [ ] B1 | `config.yaml` `modes.*.vault` | `~/Vaults/Jarvis-*` in the daemon's home | `${WIN_HOME}/Vaults/Jarvis-Work` and `...Personal`. `WIN_HOME` resolves to `/mnt/c/Users/<user>`: taken from env `JARVIS_WIN_HOME`, else `~/.jarvis/win_home` (written by `install_wsl.sh`), else from interop (`cmd.exe /c echo %USERPROFILE%` + `wslpath -u`) | 2 |
| [ ] B2 | `jarvis/outbox.py` `FM` regex `^---\n(.*?)\n---\n` | LF-only. Obsidian on Windows may write CRLF (and a BOM), so the regex misses and the item is rejected as "malformed" | Normalize before matching: strip BOM, `\r\n` to `\n`, lone `\r` to `\n`. Test: a CRLF file parses identically to LF | 2 |
| [ ] B3 | vault git repos | no `.gitattributes`; on DrvFs (`/mnt/c`) git reports every file as mode 755/777 | Vault templates get `.gitattributes` (`* text=auto eol=lf`). Install sets `core.autocrlf false` **and `core.filemode false`** [stricter: filemode noise] | 2 |
| [ ] B4 | repo root | no `.gitattributes` | Add `* text=auto eol=lf` (+ `*.ps1 text eol=crlf` so PowerShell 5.1 parses cleanly) | 2 |
| [ ] B5 | API → Windows | Linux paths are meaningless to the tray app | `jarvis/paths.py`: `to_windows(p)` uses `wslpath -w` (pure-Python `/mnt/<d>/` fallback for tests); `obsidian_uri(vault, file)` = `obsidian://open?vault=<vault dir name>&file=<vault-relative path, url-encoded>` | 2 |
| [ ] B6 | file watching | inotify does not fire on `/mnt/c` for edits made by Windows processes (Obsidian) | The "new pending item" SSE uses **polling** (5 s) of `outbox/`, never inotify | 2 |
| [ ] B7 | `jarvis/modes.py` `ROOT/env/*.env` | env files sit next to the code, wherever it was cloned | Refuse to start if an env file resolves under `/mnt/` (Windows FS), or if it is group- or world-readable **[stricter]**. `install_wsl.sh` refuses to run from a `/mnt/*` checkout | 2 |
| [ ] B8 | `PATH` in subprocess env | WSL appends the Windows PATH (`/mnt/c/...`) to interactive shells; the native `claude` installs to `~/.local/bin`, which systemd's default PATH lacks | The systemd unit sets `Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin` (no Windows dirs), so `claude` is found and Windows binaries are not on the brain's PATH | 2 |
| [ ] B9 | time | `dt.datetime.now()` (naive local) drives `max_age_hours`, daily caps, and APScheduler crons. The WSL2 clock can drift after Windows sleep, and the distro TZ may not match Windows | `install_wsl.sh` prints `timedatectl` and warns if TZ is UTC while Windows is not; README notes `sudo hwclock -s` for drift. No code change (the expiry gate is conservative when the clock is behind by at most minutes) | 2/4 |
| [ ] B10 | lifecycle | WSL VM/distro idles out when no Windows process is attached, so the daemon dies | (a) optional `vmIdleTimeout=-1` via `install.ps1` (prompted, version-checked, per brief 7.5); (b) **[stricter/robust]** the tray app keeps one hidden `wsl.exe -d <distro> -e sleep infinity` child alive while it runs | 3 |
| [ ] B11 | `systemctl --user` via `wsl.exe -e` | `XDG_RUNTIME_DIR` may be unset for non-login `wsl.exe -e`, so `systemctl --user` can't reach the user bus | Linger + `WantedBy=default.target` means the service starts with the VM anyway; the logon task still runs the start command but exports `XDG_RUNTIME_DIR=/run/user/$(id -u)` | 3 |
| [ ] B12 | `jarvis/voice.py` | `tempfile.mktemp` (race-prone) | Gone with voice.py; the client uses `tempfile.mkstemp` inside a private dir | 3 |
| [ ] B13 | `config.yaml` `repos:` | `~/code/*` | Keep them in the WSL home (ext4) for build speed. Doc only | 4 |

## C. Places a secret could cross into Windows

Secrets = every value in `env/work.env` and `env/personal.env`, MCP OAuth tokens
(Claude Code keeps them in WSL `~/.claude`), and Claude credentials.

| # | Vector | Status / fix | Phase |
|---|---|---|---|
| [ ] C1 | env files stored on the Windows FS | Blocked by B7 (startup refusal + installer refusal) | 2 |
| [ ] C2 | vault `.mcp.json` | Contains only `${VAR}` placeholders; expansion happens in the WSL process. Keep it that way. Add a startup check that `.mcp.json` contains no literal value from either env file **[stricter]** | 2 |
| [ ] C3 | API responses | `brain.ask` returns up to 300 chars of `claude` stderr; an MCP error message could echo a URL or header. `last_block` can carry `server error: <content>`. Fix: every API string passes through `redact()`, which replaces any env value (len >= 8) of either mode with `[redacted]` **[stricter]** | 2 |
| [ ] C4 | `/health` | Returns booleans and counts only; no paths, no env keys | 2 |
| [ ] C5 | API token | The only credential on Windows, by design. 32 random bytes (hex). `~/.jarvis/api_token` is created with `O_EXCL` and mode 0600; copied to `%LOCALAPPDATA%\Jarvis\api_token`, which inherits the user-only profile ACL. Compared with `hmac.compare_digest`. Failed auth is logged with client addr and path, **never the presented token** | 2 |
| [ ] C6 | Browser/CSRF/DNS-rebinding against `localhost:8765` | Bearer token required (browsers can't read it). No CORS middleware. `TrustedHostMiddleware` allows only `localhost`/`127.0.0.1` **[stricter]** | 2 |
| [ ] C7 | LAN exposure | Bind host hardcoded to `127.0.0.1`; config can change only the port. A non-loopback host in config is a startup error. Mirrored networking would expose `0.0.0.0` to the LAN, so it is never used | 2 |
| [ ] C8 | Windows client | Holds only `client.yaml` (paths, hotkeys, URL) and reads `api_token`. Never reads `\\wsl$`. Install verification greps `windows_client/` and `%LOCALAPPDATA%\Jarvis` for every env value (`python -m jarvis.verify_secrets`, run from WSL, prints key names only, never values) | 3/4 |
| [ ] C9 | `jev._haiku_fallback` | Runs `claude -p` with the daemon's full `os.environ`, cwd = repo, and without `--strict-mcp-config`, so any user-global MCP servers load. Under systemd the environ holds no secrets, but fix anyway: minimal PASSTHROUGH env + `--strict-mcp-config` **[stricter]** | 4 |
| [ ] C10 | `agent-logs/runs.log` | Writes the first 120 chars of every prompt into the vault, which now lives on the Windows FS. That is the user's own vault (same trust as the notes), not a secret. Keep, documented | doc |
| [ ] C11 | Residual, cannot fix in code | Any process running as the **same Windows user** can read WSL files via `\\wsl.localhost\Ubuntu\...` or `wsl.exe -u root cat ...`. WSL is not a security boundary against the same user. The guarantee we give is narrower: no secret is *stored on* the Windows FS, *held by* the client, or *returned by* the API. README must say this plainly | 4 |

## D. Safety and gate findings (pre-existing bugs the port must not inherit)

| # | Finding | Severity | Fix | Phase |
|---|---|---|---|---|
| [ ] D1 | `outbox.approve()` sets `status: approved` regardless of current status. "approve <id>" on an already **sent** item re-sends it (only the daily cap stops it). On an **expired** item it flips back to approved (the age gate still blocks, but state is wrong) | HIGH | `approve()` accepts only `pending`, or `approved` (a re-approve after a block). `sent`, `expired`, and anything else raise `Blocked` | 2 |
| [ ] D2 | `approve()` doesn't check `meta.mode` or `meta.id` against the request. A personal item copied into the work outbox gets marked approved (execute then blocks on mode, but the file was already mutated) | MEDIUM | `approve()` checks `mode` and `id` before writing | 2 |
| [ ] D3 | The body hash in the brief binds the **preview**, but the executor sends **`args`**. Editing `args` in Obsidian after the user heard the body would pass a body-only hash | HIGH | `/outbox` also returns `item_sha256` = sha256 of canonical JSON `{mode, server, tool, args, body}`. `/approve` requires both hashes. `approve()` stores `approved_sha256`, and `check_gates` blocks if the current item hash differs ("changed after approval") **[stricter]** | 2 |
| [ ] D4 | Path traversal: an API-supplied `id` goes into `vault/outbox/{id}.md` | HIGH (API is new) | `id` must match `^[A-Za-z0-9_-]{1,128}$`; resolved path must stay inside `outbox/` | 2 |
| [ ] D5 | Pre-emptive `reconfirm`: text "approve <id> reconfirm" can skip the second confirmation before any tone check ran | MEDIUM | On `/approve`, `reconfirm=true` is accepted only if the item's `last_block` is a tone flag **[stricter]**. Text channels are unchanged (brief: keep text commands) | 2 |
| [ ] D6 | Voice: a spoken prefix ("personal, ...") overrides the hotkey mode. The brief requires that `/utterance` in work mode cannot reach personal MCP | HIGH | Voice mode is hotkey-bound. A prefix that disagrees returns `needs_mode`. The follow-up must come from the matching hotkey (`mode_confirmed=true` + that hotkey's mode). Jev can still only *ask*, never switch | 2 |
| [ ] D7 | Voice control commands go through Jev mode detection first; "what's pending?" (with `?`) is ambiguous and fails the exact-match check | MEDIUM (blocks DoD) | Control commands (`pending`, `what's pending`, `read <id>`) are normalized (case, trailing punctuation) and use the bound mode without Jev | 2 |
| [ ] D8 | Voice "approve <id>" over `/utterance` would approve without read-back | HIGH | `handle()` refuses approve commands when `channel == "voice"`. Voice approval exists only via `/approve` + hashes. Slack/Telegram text approve unchanged | 2 |
| [ ] D9 | Approve + execute race: another writer could edit between the two steps | LOW | Per-mode lock around the API approve+execute; D3's `approved_sha256` re-check closes it anyway | 2 |
| [ ] D10 | `channels.start_*` raise on empty tokens; with `Restart=always` that crash-loops, and `/health` never comes up before env files are filled | MEDIUM | Skip a channel (log a warning) when its tokens are empty | 2 |
| [ ] D11 | Unhandled exceptions in `check_gates` (Jev fallback failure) fail closed but surface as a 500 | LOW | API catches, returns `{"sent": false, "blocked": "internal error"}`, logs details server-side | 2 |

### D12-D19: added after the Phase 1 review

| # | Finding | Severity | Fix | Phase |
|---|---|---|---|---|
| [ ] D12 | The agent writes both `body` and `args`, and only `args` is sent. A body-only read-back lets "See you at 5" approve different `args` | HIGH | The server renders a deterministic **readback** from `{mode, server, tool, args}` (every arg key and value). The client speaks the readback, not the body. `/approve` requires `readback_sha256` (hash of the spoken text) in addition to `body_sha256` (brief). This replaces D3's `item_sha256` on the wire | 2 |
| [ ] D13 | An agent can pre-set gate fields: `reconfirmed: true` skips the tone gate; a future `created` never expires; `approved_at` | HIGH | `approve()` always rewrites `approved_at`, drops `reconfirmed` unless this call's reconfirm is valid, and stores `approved_sha256` = hash of canonical `{mode, server, tool, args, body, created}`. `check_gates` blocks if `created` is more than 5 min in the future, and if the current hash != `approved_sha256` (or it is missing) | 2 |
| [ ] D14 | `Read/Glob/Grep` in vault sessions have no path scope, so a work session can read `env/personal.env`, the other vault, or `~/.claude` credentials, and return them via `/utterance` to Windows | HIGH | Scope allowed tools to the session's own vault: `Read(//<vault>/**)`, etc. Add explicit denies (deny wins) for the repo dir (env, mcp configs), the other vault, `~/.claude/**`, and `~/.jarvis/**`. Test in `test_mode_isolation` | 2 |
| [ ] D15 | The vault `.mcp.json` sits on /mnt/c and is writable by the vault agent (and by Windows). The executor substitutes the Bearer token into whatever URL it names, so exfiltration is possible. A planted `.claude/settings.json` `hooks` block runs shell commands despite Bash being denied | HIGH | MCP configs move to the repo: `mcp/work.mcp.json`, `mcp/personal.mcp.json` (WSL ext4). The vault copies are deleted. Brain and executor use only the repo copy. Deny Write/Edit on `<vault>/.claude/**` and `<vault>/.mcp.json`. `brain.ask` refuses to run if `<vault>/.claude/settings*.json` contains `hooks` | 2 |
| [ ] D16 | Double send: MCP call first, `sent` recorded after. A timeout or `isError` leaves `approved`, so a re-approve re-sends | MEDIUM | Write `status: sending` before the call. Success becomes `sent`. An exception or `isError` becomes `status: failed` + `last_block`. `approve()` refuses `sending`/`failed` (a human resolves it in Obsidian) | 2 |
| [ ] D17 | Obsidian-set `status: approved` has no hash | MEDIUM | Nothing auto-executes. The unused `run_approved` (which would execute without a hash) is removed. Every execution goes through `approve()` (API with hashes, or the Slack/Telegram text command), which always sets `approved_sha256` | 2 |
| [ ] D18 | Implicit encodings; no `LANG` in the unit | LOW | `encoding="utf-8"` on every outbox read/write; `Environment=LANG=C.UTF-8` in the unit | 2 |
| [ ] D19 | `brain.plan_build` runs `claude` with mode secrets and no `--strict-mcp-config` | LOW | Add `--mcp-config <repo mode config> --strict-mcp-config` | 4 |

Also for README (Phase 4): the `/mcp` OAuth step from WSL needs a browser handoff (`wslu`/`wslview`, or copy the printed URL into a Windows browser; the callback on localhost is forwarded).

## E. Design decisions for implementers (so both implementers produce one system)

- **Hashes.** `body_sha256 = sha256(body.encode("utf-8"))`, where `body` is exactly what
  `read_item` returns (LF-normalized, stripped). `readback` = the server-rendered text of what
  will actually be sent (D12). `readback_sha256` = its sha256. The client speaks `readback` in
  full, hashing the exact string it passes to `speak()`, before TTS-only transforms. It checks that
  hash against the server's `readback_sha256` before speaking, and POSTs both its own readback hash
  and the `body_sha256` it received. **[stricter than brief: the brief binds only the body]**
- **`/outbox` item.** `{id, mode, status, preview (full body), first_line, body_sha256, readback, readback_sha256, obsidian_uri, windows_path, last_block, tone_flagged}`.
  It lists `status == pending` and "tone-flagged" (`status == approved` and `last_block` starts with `tone flag`).
- **`/approve` body.** `{mode, id, body_sha256, readback_sha256, reconfirm}`. 404 unknown id; 409 hash
  mismatch or status not approvable; 400 bad reconfirm. 200 returns `{result, sent, blocked}`. Every
  `check_gates` gate still runs inside `outbox.execute`.
- **`/utterance` body.** `{text, mode, mode_confirmed?}` → `{reply, mode_used, needs_mode}`.
- **Events (SSE `/events`).** `event: <type>`, `data: <json>`. Types: `pending_new`, `item_sent`,
  `item_blocked`, `schedule_done`. Payload `{mode, id?, first_line?, obsidian_uri?, reason?, job?}`.
  Heartbeat comment every 15 s.
- **Module layout (WSL).** `jarvis/api.py` (app factory + serve), `jarvis/events.py` (thread-safe bus,
  outbox poller), `jarvis/paths.py` (WIN_HOME, wslpath, obsidian URI), `jarvis/handler.py`
  (`make_handler(modes)`, moved out of `main.py` so tests can inject modes), `jarvis/verify_secrets.py`.
- **Headless.** The daemon is always headless now. `--headless` is accepted (and used by the systemd unit)
  for explicitness; local voice is removed, not hidden behind a flag.
- **Client layout.** `windows_client/jarvis_client/`: `config.py`, `api.py` (httpx + SSE),
  `flow.py` (pure voice state machine: approve/confirm, needs_mode; no hardware imports),
  `stt.py`, `tts.py`, `notify.py`, `tray.py`, `__main__.py`. Hardware and Windows-only imports stay local to
  their modules so `flow.py`/`api.py` unit-test under Linux pytest.
- **Voice approve UX.** "approve <id-or-slug>" or "approve" (only when exactly one approvable item
  exists). Match is on the normalized id/slug and must be unique. The confirm window is 60 s; any other
  utterance, a mode change, or a timeout cancels and speaks "Nothing was sent."
- **Client logging.** Logs never contain utterance or reply text in any mode. The brief requires this for
  personal mode; **[stricter]** we apply it to both. Temp WAVs go to a private dir and are
  deleted in `finally`.

## F. Phase plan and owners

| Phase | Scope | Owner | Gate |
|---|---|---|---|
| 1 | This file | auditor (Opus, orchestrator) | reviewer (Opus) |
| 2 | `api.py`, `events.py`, `paths.py`, `handler.py`, `--headless`, CRLF, D1-D11, systemd unit, `install_wsl.sh`, config, requirements, `.gitattributes`, backend tests | implementer-opus | reviewer: tests green; no gate bypass; C1-C7 |
| 3 | `windows_client/` (tray, hotkeys, STT, TTS, flow, toasts, keepalive), `install.ps1`, logon task, client tests | implementer-sonnet | reviewer: flow tests; C8 |
| 4 | Delete `voice.py`, plist, `install.sh`; C9; `verify_secrets`; README rewrite | implementer-sonnet + docs-writer | reviewer: full suite, secret grep, docs accuracy |

**Can't be verified from this macOS workstation:** `curl.exe` against the forwarded port,
Task Scheduler at logon, Obsidian URI activation, whisper/Piper on Windows, and reboot
recovery. Each phase's tests cover the logic with mocks. The Windows-side items are listed
in README under "First-run verification" for the user to run once.

## G. Test matrix (brief section 10)

| Brief | Test file |
|---|---|
| 10.1 401 missing/wrong token, 127.0.0.1 bind | `tests/test_api_auth.py` |
| 10.2 hash mismatch, gates still run, expired stays blocked | `tests/test_api_approve.py` |
| 10.3 mode isolation (approve + utterance env/`--mcp-config`) | `tests/test_mode_isolation.py` |
| 10.4 CRLF == LF | `tests/test_outbox_crlf.py` |
| 10.5 client confirm/hash/no-transcript-on-disk | `windows_client/tests/test_flow.py` |
| 10.6 no env values on the Windows side | `tests/test_verify_secrets.py` + install-time `python -m jarvis.verify_secrets` |
| D1, D2, D4, D5, D6, D7, D8 | `tests/test_outbox_approve.py`, `tests/test_handler.py` |

## Sign-off log

- Phase 1: SIGN-OFF (Opus reviewer, 2026-09-24) after revision D12-D19. Carry-forward to the Phase 2 gate: the D14 deny list must be complete (other vault incl. realpath, repo, `~/.claude/**`, `~/.claude.json`, `~/.jarvis/**`, `~/.ssh/**`, the `%LOCALAPPDATA%\Jarvis` path), because read-only tools need no approval and allow-scoping does not confine them; prove it with a real `claude -p` run.
- Phase 2: SIGN-OFF (two Opus reviewers: security/gates + integration, 2026-09-24) after a fix round covering H1-H3, M1-M4, L1-L3 and the SSE shutdown hang. Post-sign-off hardening: HMAC approve code, redact-before-truncate, text reconfirm requires a ledger tone flag.
- Phase 3: SIGN-OFF (Opus reviewer, 2026-09-24) after a fix round (H1 PyInstaller entry, H2 422 transcript leak, M1-M5). Two MEDIUMs + one LOW fixed in Phase 4.
- Phase 4:
