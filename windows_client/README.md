# Jarvis Windows tray client

The Windows half of Jarvis: a tray icon, global hotkeys for push-to-talk, local speech-to-text
(whisper.cpp) and text-to-speech (Piper or Windows SAPI), and toast notifications for pending
Obsidian drafts. Two client profiles (PLAN.md AD16):

- **`aws`** (default): two daemons on the EC2 instance, one per mode, reached over Tailscale at
  `http://jarvis:8781` (work) and `http://jarvis:8782` (personal). API tokens are fetched from AWS
  Secrets Manager at request time and held in memory only -- never written to disk. Vaults sync
  over Syncthing (tailnet only).
- **`wsl`**: the original local-WSL2 dev profile, a single `api_url`/`token_path` pair.

Either way it never reads secrets or env files directly, and never crosses into `\\wsl$` under the
wsl profile -- it only ever holds `client.yaml` and (wsl profile) the bearer token file.

## Install

From an ordinary PowerShell 5.1+ prompt, inside `windows_client\`:

```powershell
.\install.ps1                                                                     # aws profile (default)
.\install.ps1 -SyncthingWorkDeviceId <id> -SyncthingPersonalDeviceId <id>         # aws profile, also wires up Syncthing
.\install.ps1 -ClientProfile wsl                                                  # legacy local-WSL2 profile
```

This downloads whisper.cpp (and, if you opt in, Piper), builds or installs `JarvisTray.exe`, and
writes `client.yaml` if it doesn't exist yet. Under `-ClientProfile aws` (default) it also installs
the AWS CLI v2, Tailscale, and Syncthing (via `winget`, falling back to each vendor's own installer
if `winget` isn't available or the install fails; a Scheduled Task keeps Syncthing running at
logon, since unlike Tailscale's installer it registers no Windows service of its own) and, given
`-SyncthingWorkDeviceId`/`-SyncthingPersonalDeviceId` (get each from its own instance on the box --
`syncthing cli show system` run as `jarvis-work` / `jarvis-personal`, or that instance's own GUI --
each validated as a Syncthing device id), registers `Jarvis-Work` with the work device and
`Jarvis-Personal` with the personal device (they are separate Syncthing instances, DESIGN.md AD8)
under `%USERPROFILE%\Vaults\`, over the local Syncthing's REST API. Adding the *workstation's* own
device to each instance happens on the server side (a runbook step); this script only ever writes
to the workstation's own local Syncthing config. It then prints the one-time
`aws configure sso --profile <profile>` command to run before the tray can fetch API tokens. Under
`-ClientProfile wsl` it instead offers to set `vmIdleTimeout=-1` in `.wslconfig` and verifies the
WSL-side daemon and its `/health` endpoint. Either way it registers a logon Scheduled Task named
`Jarvis`, and is safe to re-run: every step skips work that's already done, and `client.yaml` is
never overwritten once it exists (edit it by hand to change profiles).

## Build only

```powershell
.\build.ps1
```

Builds `dist\JarvisTray.exe` with PyInstaller (`--onefile --noconsole`), including the hidden
imports pystray/pynput need for their Windows backends. Requires Python 3.12 (`py -3.12`).

## Config: `%LOCALAPPDATA%\Jarvis\client.yaml`

Created with defaults on first run of the client (or by `install.ps1`, whichever runs first). A
client-created file defaults `profile` to `wsl` (assumed to predate this change); `install.ps1`
always writes `profile: aws` explicitly unless run with `-ClientProfile wsl`.

| Key | Default | Notes |
|---|---|---|
| `profile` | `wsl` (client-written) / `aws` (install.ps1-written) | Picks the fields below. |
| `api_url_work` / `api_url_personal` | `http://localhost:8781` / `:8782` | Per-mode daemon URLs (one daemon per mode everywhere, PLAN.md AD4/AD13); `install.ps1` (aws profile) writes `http://jarvis:8781` / `:8782`. |
| `token_source` | `file` | `file` (below) or `secretsmanager` (aws profile). |
| `aws_profile` | `""` | IAM Identity Center profile (JarvisClient permission set) for `aws secretsmanager get-secret-value`; aws profile only. |
| `aws_region` | `us-east-1` | Region passed as `--region` on every `aws secretsmanager get-secret-value` call (PLAN.md AD34); aws profile only. |
| `token_secret_work` / `token_secret_personal` | `jarvis/work/api-token` / `jarvis/personal/api-token` | Secrets Manager secret ids; aws profile only. |
| `api_url` | `http://localhost:8765` | Legacy single-daemon URL; kept only so a pre-existing file round-trips, no longer read by the two-daemon code path. |
| `token_path` | `%LOCALAPPDATA%\Jarvis\api_token` | Used when `token_source: file`. Re-read on every request/reconnect. The same file backs both modes' `FileToken`, a pre-existing limitation of the local/wsl token-copy path (see `jarvis.api.copy_token_to_windows`), not something this client can fix on its own. |
| `whisper_exe` | `%LOCALAPPDATA%\Jarvis\whisper\whisper-cli.exe` | |
| `whisper_model` | `%LOCALAPPDATA%\Jarvis\whisper\ggml-base.en.bin` | |
| `piper_exe` / `piper_voice` | `null` | Set only if you installed Piper; falls back to SAPI. |
| `hotkey_work` / `hotkey_personal` | `<ctrl>+<alt>+w` / `<ctrl>+<alt>+p` | pynput GlobalHotKeys syntax. |
| `wsl_distro` | `Ubuntu` | wsl profile only; passed to every `wsl.exe -d <distro>`. |
| `vault_work` / `vault_personal` | `Jarvis-Work` / `Jarvis-Personal` | Obsidian vault names. |
| `sample_rate` | `16000` | Recording sample rate (Hz). |

Tokens under `token_source: secretsmanager` are fetched via a subprocess call to
`aws secretsmanager get-secret-value --profile <aws_profile> --secret-id <token_secret_work|
token_secret_personal> --query SecretString --output text --region <aws_region>` (default
`us-east-1`), cached in memory, and refetched once whenever a request comes back `401` -- never
written to disk, never logged.

## Using it

- **Ctrl+Alt+W** / **Ctrl+Alt+P**: press to start recording (work/personal), press again to stop.
  A beep marks the start of recording; the tray icon turns blue (work) or amber (personal), then
  purple while the daemon thinks.
- **Tray menu**: open a vault in Obsidian, list pending items by mode (spoken summary + a toast
  per item with an "Open in Obsidian" button), restart the WSL daemon, or quit.
- **Approving a draft by voice**: say "approve `<name>`" (or "send `<name>`"), or just "approve"
  when exactly one item is pending. Jarvis reads the draft back to you, then says "confirm" (or,
  for a tone-flagged item, "confirm reconfirm") within 60 seconds -- otherwise nothing is sent.
- **Wrong-mode utterance**: if what you said sounds like it belongs to the other mode, Jarvis says
  "That sounds like `<mode>`. Say confirm to send it there." Saying "confirm" within 60 seconds
  re-sends your original words to that mode's daemon; anything else (or the timeout) cancels with
  "Nothing was sent."
- **Grey icon**: both daemons are unreachable. Jarvis says "Jarvis is offline" once, then keeps
  polling `/health` every 10 seconds until at least one comes back. The tray tooltip always lists
  each mode's status individually (e.g. "Jarvis (work: ok, personal: offline)"), even when the
  icon itself isn't grey because the other mode is still up.

## Troubleshooting

- **Nothing happens on a hotkey / no beep**: check `%LOCALAPPDATA%\Jarvis\client.log`. Logs never
  contain utterance, reply, or readback text -- only event kinds, modes, lengths, and status
  codes, so they're safe to share when asking for help.
- **Grey icon that never clears (wsl profile)**: run `wsl.exe -d <distro> -e systemctl --user
  status jarvis` from a terminal, and re-run `install.ps1 -ClientProfile wsl` (verification step) to
  check `/health` directly.
- **Grey icon that never clears (aws profile)**: check Tailscale is connected
  (`tailscale status`) and that `make status` (repo root) shows both `jarvis@work` and
  `jarvis@personal` active on the instance; the tray tooltip shows which mode(s) are down.
- **Syncthing folders never registered**: re-run `install.ps1` with `-SyncthingWorkDeviceId <id>`
  and/or `-SyncthingPersonalDeviceId <id>` (get each id from its own instance on the box:
  `syncthing cli show system` run as `jarvis-work` / `jarvis-personal`), or add the folders by
  hand at `http://127.0.0.1:8384`.
- **Register-ScheduledTask fails**: some environments require an elevated PowerShell prompt for
  Task Scheduler even with `-RunLevel Limited`; try running `install.ps1` as Administrator once.
- **No sound / TTS silent**: if `piper_exe`/`piper_voice` are set but wrong, Jarvis logs a
  warning and falls back to SAPI automatically; check the paths in `client.yaml`.
- **WSL VM keeps dying**: the tray keeps a hidden `wsl.exe ... sleep infinity` process alive while
  it runs; if you still see idle-outs, re-run `install.ps1` and accept the `.wslconfig` prompt
  (requires Windows 11 and WSL >= 2.0.0).

## Tests

```bash
python -m pytest -q windows_client/tests
```

Runs anywhere -- `flow.py`, `api.py`, and `config.py` have no Windows-only imports, and the
hardware modules (`stt`, `tts`, `tray`, `notify`) import pystray/pynput/sounddevice/pyttsx3/
win11toast lazily inside functions so they can still be imported (and partly tested, e.g. the
WAV-deletion logic in `stt.py`) without those packages installed.
