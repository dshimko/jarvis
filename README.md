# Jarvis

Personal and work assistant built on Obsidian (memory), Claude Code (brain and subagents), Jev
(fast routing and safety checks), MCP (hands), and local speech. The brain runs headless under
systemd, either in WSL2 Ubuntu (development) or on a dedicated EC2 instance (always-on, see
"Deployments" below); the UI is a native Windows tray app that talks to it over loopback HTTP or
Tailscale.

## Shape

Two modes, two vaults, two secret files, two MCP configs. Nothing is shared.
Work: Sparko Slack, work Gmail, Atlassian, ClickUp. Channels: Slack DM, voice (Ctrl+Alt+W).
Personal: personal Gmail, OFW MCP server. Channels: Telegram, voice (Ctrl+Alt+P).

MCP configs are repo files, not vault files: `mcp/work.mcp.json` and `mcp/personal.mcp.json`, on
the WSL (ext4) filesystem. They hold only `${VAR}` placeholders, expanded from that mode's env
file at call time. The vault has no `.mcp.json` of its own.

| Side | Runs |
|---|---|
| WSL2 (Ubuntu, systemd) | The daemon: API, brain (`claude -p` per mode), outbox executor, Jev client, Slack/Telegram channels, schedules. Headless, no display or audio. |
| Windows | The tray app: global hotkeys, mic capture, whisper.cpp (STT), Piper or SAPI (TTS), toast notifications, Obsidian. Holds only `client.yaml` and the bearer token. |

Request flow: channel -> router (channel binds mode; Jev only fills gaps and asks when unsure) ->
Jev picks a subagent -> `claude -p` in that mode's vault with read-only tools -> reply (spoken if
voice, over the loopback API).

Write flow: agents can only write `outbox/<id>.md`. Approve it in Obsidian is not enough by
itself: nothing executes without a bound read-back. By text (Slack/Telegram), send `read <id>` to
get the read-back plus an 8-char code, then `approve <id> <code>` (add ` reconfirm` only after the
tone check flags an item). By voice, say "approve `<id>`" (or just "approve" when one item is
pending); Jarvis speaks the read-back, then you say "confirm" (or "confirm reconfirm" for a
tone-flagged item) within 60 seconds, and the client posts both `body_sha256` and
`readback_sha256` to `/approve`. Either path binds the exact `args` that will be sent, not just
the note body. No model sits in the send path: the executor calls the MCP tool directly with the
args from the file.

## Isolation and safety guarantees (enforced in code, not prompts)

1. Each `claude -p` runs with cwd in its own vault, `--mcp-config` pointed at the repo's copy for
   that mode, and `--strict-mcp-config` (ignores any user/global MCP servers).
2. Subprocess env contains only that mode's secrets plus a fixed passthrough list (`PATH`, `HOME`,
   `USER`, `LANG`, `SHELL`, `TMPDIR`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`). Each mode
   runs as its own daemon (`python -m jarvis.main --mode work|personal`) that opens only its own env
   file, so the other mode's tokens do not exist in the process.
3. A secret value that appears in both env files, or any env value written literally into
   `mcp/work.mcp.json` or `mcp/personal.mcp.json`, stops the daemons from starting: every
   `jarvis@<mode>` start first runs `python -m jarvis.secrets_check env/work.env env/personal.env` as a
   separate process (`ExecStartPre`), and `install_wsl.sh` runs the same check and leaves the units
   disabled on a violation. The daemon itself never opens the other mode's env file; it still refuses to
   start if its own MCP config holds one of its own values, or if its env file lives under `/mnt/`
   (Windows FS) or is group/world readable. On AWS the root `jarvis-secrets` tool refuses to write
   either env file on a shared value.
4. Tool path denies (`jarvis/brain.py`) block reads and writes outside a session's own vault: the
   other mode's vault, the repo (env files, MCP configs), `~/.claude`, `~/.claude.json`,
   `~/.jarvis`, `~/.ssh`, `/proc`, `/run/user`, `/dev/shm`, and `%LOCALAPPDATA%\Jarvis`. Deny wins
   over allow, and read-only tools (Grep/Glob) are covered by the same Read denies since
   allow-scoping alone does not confine them.
5. Write tools are always in `--disallowedTools`; Bash, WebFetch, and WebSearch are disallowed too.
   A vault session refuses to run at all if `.claude/settings*.json` sets a risky key (`hooks`,
   `permissions`, `env`, etc.) or `.git/config` sets `fsmonitor`/`hooksPath`. `--settings` also
   passes `disableAllHooks: true` as defense in depth.
6. The approval hash binds the read-back the approver actually saw. `approved_sha256` is a hash of
   `{mode, server, tool, args, body, created}`; the executor re-checks it before sending, so
   editing `args` after approval blocks the send. Text approval requires the code from `read <id>`;
   voice approval requires both `body_sha256` and `readback_sha256` from the spoken read-back.
7. Daemon-side state under `~/.jarvis` (0600, WSL ext4, never in the vault): the tone-flag ledger
   (`tone_flags.json`) and the send ledger (`sends.log`) that drives the daily write cap and the
   "sent"/"failed" bookkeeping the vault agent cannot influence.
8. Each daemon's API binds exactly one address: `127.0.0.1` locally (work 8781, personal 8782), the
   Tailscale IPv4 on AWS (no Tailscale address is a startup error, never a fallback to `0.0.0.0`); a
   configured `api.host` is a startup error. It requires a bearer token (32 random bytes, `hmac.compare_digest`, never logged),
   and has no CORS middleware plus a `TrustedHostMiddleware` allowing only `localhost`/`127.0.0.1`
   (AWS: the Tailscale IP, `jarvis`, and `*.ts.net`).
9. Every string the API can return (`/health`, `/utterance`, `/outbox`, `/approve`, SSE events)
   passes through `redact()`, which replaces any env value (length >= 8) of the daemon's mode with
   `[redacted]`.
10. **AWS only: one OS user per mode, not just a subprocess.** `jarvis-work` (uid 2001) and
    `jarvis-personal` (uid 2002) are separate Linux users, each with its own home (0700), systemd
    unit, and Syncthing instance. Root-only IMDS access (iptables, checked at every boot), a
    root-only secrets-sync service, `hidepid=invisible` on `/proc`, and `ProtectProc=invisible` on
    every mode unit keep one mode's env, OAuth tokens, and process list unreadable to the other --
    a stronger boundary than the local WSL per-daemon isolation above, which relies on one Linux
    user running two separate daemons. See `infra/RUNBOOK.md`.

**Residual risk (read this plainly):** any process running as the *same Windows user* can reach
WSL files via `\\wsl.localhost\Ubuntu\...` or `wsl.exe -u root cat ...`. WSL is not a security
boundary against that user. The guarantee Jarvis gives is narrower: no secret is *stored on* the
Windows filesystem, *held by* the Windows client, or *returned by* the API.

## Deployments

Two ways to run Jarvis, same app code and the same safety behavior either way:

- **Local WSL2 (development).** One Windows box, WSL2 Ubuntu, two systemd **user** units,
  `jarvis@work` and `jarvis@personal`, each bound to `127.0.0.1` on its own port (`8781` work,
  `8782` personal). Each unit's `ExecStartPre` runs `jarvis.secrets_check` before every start, so a
  secret shared across modes fails the start instead of leaking. See "Setup" below.
- **AWS (always-on).** One EC2 instance, `jarvis-work` and `jarvis-personal` as separate Linux OS
  users (isolation item 10 above), APIs reachable only over Tailscale, secrets in AWS Secrets
  Manager, deploys and rollbacks over SSM. Full procedure, start to finish, in
  [`infra/RUNBOOK.md`](infra/RUNBOOK.md).

Logging is the same on both: structured JSON, one object per line, with content fields (body,
text, draft, preview, snippet, subject, transcript, and more) dropped before any handler ever
sees them -- nothing but ids, event types, durations, status, and error class ever leaves the
process, let alone the machine.

## Setup (local WSL, development)

For the AWS always-on deployment, skip to [`infra/RUNBOOK.md`](infra/RUNBOOK.md) instead.

1. **WSL2 + systemd.** Install WSL2 Ubuntu. Make systemd PID 1 by adding to `/etc/wsl.conf`:
   ```
   [boot]
   systemd=true
   ```
   Then, from Windows PowerShell (not the WSL shell): `wsl.exe --shutdown`, reopen Ubuntu.
2. Clone the repo into your **WSL Linux home** (e.g. `~/jarvis`), never under `/mnt/c`.
   `./install_wsl.sh` refuses to run from `/mnt/*`; secrets must not live on the Windows FS.
3. Run `./install_wsl.sh`. It checks WSL2/systemd, resolves your Windows `USERPROFILE`/
   `LOCALAPPDATA` via interop, installs apt/pip prerequisites and Claude Code, copies the vault
   templates to `%USERPROFILE%\Vaults\Jarvis-{Work,Personal}`, creates the env files from their
   `.example`s, installs the systemd user unit, and enables linger so the service survives logout.
4. Fill in real secrets: `env/work.env` and `env/personal.env`.
5. Start the daemons: `systemctl --user start jarvis@work jarvis@personal`.
6. In **each** vault directory, run `claude` once and use `/mcp` to authorize OAuth servers
   (Atlassian, ClickUp). A browser should open on Windows automatically; if it doesn't, `sudo apt
   install wslu` (for `wslview`) or copy the printed URL into a Windows browser by hand -- the
   localhost OAuth callback is forwarded either way.
7. Fill in the exact `read_tools`/`write_tools` MCP names in `config.yaml` (run `claude mcp list`
   plus a tool listing in each vault).
8. Open both vaults in Obsidian from `C:\Users\<you>\Vaults`.
9. On Windows, run `windows_client\install.ps1`. It downloads whisper.cpp (and, if you opt in,
   Piper), builds or installs `JarvisTray.exe`, writes `client.yaml` if absent, registers a
   logon Scheduled Task named `Jarvis`, optionally sets `vmIdleTimeout=-1` in `.wslconfig`, and
   verifies the daemon and `/health`.

## First-run verification checklist

Run these once, from Windows -- they can't be exercised from a Mac:

- `curl.exe -H "Authorization: Bearer $(Get-Content $env:LOCALAPPDATA\Jarvis\api_token)" http://localhost:8781/health` (work) and the same on `8782` (personal)
- The hotkeys: Ctrl+Alt+W (work) and Ctrl+Alt+P (personal) start/stop a recording; a beep marks
  the start, the tray icon changes color while recording and while the daemon thinks.
- Say (or type in Slack/Telegram) "what's pending" / "pending" and confirm you get a reply.
- An approve round-trip by voice: say "approve", hear the read-back, say "confirm" within 60
  seconds, confirm the item's status moves to `sent` in the vault.
- A toast with an "Open in Obsidian" button appears for a new pending item.
- Reboot recovery: log off/on (or restart Windows) and confirm the tray icon comes up and the
  daemon answers `/health` without manual intervention.
- `python -m jarvis.verify_secrets` from WSL -- prints key names only, never values, and exits 0
  when neither env file's secrets appear anywhere under `windows_client/` or
  `%LOCALAPPDATA%\Jarvis`.

## Voice and text commands

Control commands run in code, never the model: `pending` (also "what's pending", "outbox"),
`read <id>`, `approve <id> <code>` / `send <id> <code>` (add `reconfirm` only after a tone flag).
Voice never accepts `approve`/`send` as a spoken control command over `/utterance` -- voice
approval only happens through the tray's read-back + confirm flow (say "approve `<id>`" or just
"approve" when exactly one item is pending, then "confirm" / "confirm reconfirm").

## Operations

- **Logs.** Daemons: `journalctl --user -u jarvis@work` and `-u jarvis@personal` (one JSON object per
  line; content fields are dropped before any handler, `JARVIS_LOG_FORMAT=text` for readable dev output). Client: `%LOCALAPPDATA%\Jarvis\client.log`
  (never contains utterance, reply, or read-back text -- only event kinds, modes, lengths, and
  status codes).
- **Restart.** `systemctl --user restart jarvis@work jarvis@personal`, or the tray menu's "restart the WSL daemon".
- **Clock drift.** The WSL2 clock can drift after Windows sleep/resume; if schedules or the daily
  cap look off, run `sudo hwclock -s` inside WSL.
- **Idling.** The WSL VM idles out when no Windows process is attached to it. `install.ps1` can
  set `vmIdleTimeout=-1` in `.wslconfig` (Windows 11, WSL >= 2.0.0 only); independent of that, the
  tray keeps one hidden `wsl.exe -d <distro> -e sleep infinity` child alive the whole time it runs,
  so the VM (and the daemon) stays up even without the `.wslconfig` change.
- **Networking.** Locally each API binds `127.0.0.1` only, by design -- a non-loopback host is a startup
  error, since mirrored networking would expose `0.0.0.0` to the LAN. If localhost forwarding from
  Windows to WSL2 ever stops working, `networkingMode=mirrored` in `.wslconfig` is the fallback,
  but only as a last resort: the API's loopback bind is what keeps it off the LAN, so mirrored mode
  changes exposure and should not be reached for casually.

## Jev

Used for mode routing, subagent routing, a cross-mode leak check, and an OFW tone check. It can
only block or ask, never send. Falls back to Claude Haiku (`claude -p --model haiku`) if
unavailable. Confirm the request shape in `jarvis/jev.py` against current TypeSafe docs.

## Builder

The builder agent writes `tasks/<id>.md`. `brain.plan_build` runs Claude Code in the repo, in plan
mode only (`--permission-mode plan`), with `--mcp-config`/`--strict-mcp-config` for that mode.
Execution stays manual until you trust it.

## Tests

From WSL, in the repo root:

```bash
pytest
```

`pytest.ini` runs both suites (`tests/` for the daemon, `windows_client/tests/` for the client
logic that has no Windows-only imports -- `flow.py`, `api.py`, `config.py`, and the
hardware-adjacent modules' pure logic). One test file, `tests/test_claude_cli.py`, runs the real
`claude` CLI and is opt-in (costs a few cents of Haiku calls):

```bash
JARVIS_RUN_CLAUDE_TESTS=1 pytest -m claude_cli
```
