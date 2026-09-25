# Jarvis Windows tray client

The Windows half of Jarvis: a tray icon, global hotkeys for push-to-talk, local speech-to-text
(whisper.cpp) and text-to-speech (Piper or Windows SAPI), and toast notifications for pending
Obsidian drafts. It talks to the Jarvis daemon running in WSL2 over `http://localhost:8765`
(see `../PORTING.md` section E and the Phase 2 API contract). It never reads secrets or env
files, and never crosses into `\\wsl$` -- it only holds `client.yaml` and the bearer token.

## Install

From an ordinary PowerShell 5.1+ prompt, inside `windows_client\`:

```powershell
.\install.ps1
```

This downloads whisper.cpp (and, if you opt in, Piper), builds or installs `JarvisTray.exe`,
writes `client.yaml` if it doesn't exist yet, registers a logon Scheduled Task named `Jarvis`,
optionally sets `vmIdleTimeout=-1` in `.wslconfig`, and finally verifies the WSL-side daemon and
its `/health` endpoint. It is safe to re-run: every step skips work that's already done.

## Build only

```powershell
.\build.ps1
```

Builds `dist\JarvisTray.exe` with PyInstaller (`--onefile --noconsole`), including the hidden
imports pystray/pynput need for their Windows backends. Requires Python 3.12 (`py -3.12`).

## Config: `%LOCALAPPDATA%\Jarvis\client.yaml`

Created with defaults on first run of the client (or by `install.ps1`, whichever runs first).

| Key | Default | Notes |
|---|---|---|
| `api_url` | `http://localhost:8765` | The daemon's loopback API. |
| `token_path` | `%LOCALAPPDATA%\Jarvis\api_token` | Re-read on every request/reconnect. |
| `whisper_exe` | `%LOCALAPPDATA%\Jarvis\whisper\whisper-cli.exe` | |
| `whisper_model` | `%LOCALAPPDATA%\Jarvis\whisper\ggml-base.en.bin` | |
| `piper_exe` / `piper_voice` | `null` | Set only if you installed Piper; falls back to SAPI. |
| `hotkey_work` / `hotkey_personal` | `<ctrl>+<alt>+w` / `<ctrl>+<alt>+p` | pynput GlobalHotKeys syntax. |
| `wsl_distro` | `Ubuntu` | Passed to every `wsl.exe -d <distro>`. |
| `vault_work` / `vault_personal` | `Jarvis-Work` / `Jarvis-Personal` | Obsidian vault names. |
| `sample_rate` | `16000` | Recording sample rate (Hz). |

## Using it

- **Ctrl+Alt+W** / **Ctrl+Alt+P**: press to start recording (work/personal), press again to stop.
  A beep marks the start of recording; the tray icon turns blue (work) or amber (personal), then
  purple while the daemon thinks.
- **Tray menu**: open a vault in Obsidian, list pending items by mode (spoken summary + a toast
  per item with an "Open in Obsidian" button), restart the WSL daemon, or quit.
- **Approving a draft by voice**: say "approve `<name>`" (or "send `<name>`"), or just "approve"
  when exactly one item is pending. Jarvis reads the draft back to you, then says "confirm" (or,
  for a tone-flagged item, "confirm reconfirm") within 60 seconds -- otherwise nothing is sent.
- **Grey icon**: the daemon is unreachable. Jarvis says "Jarvis is offline" once, then keeps
  polling `/health` every 10 seconds until it comes back.

## Troubleshooting

- **Nothing happens on a hotkey / no beep**: check `%LOCALAPPDATA%\Jarvis\client.log`. Logs never
  contain utterance, reply, or readback text -- only event kinds, modes, lengths, and status
  codes, so they're safe to share when asking for help.
- **Grey icon that never clears**: run `wsl.exe -d <distro> -e systemctl --user status jarvis`
  from a terminal, and re-run `install.ps1` step 7 (verification) to check `/health` directly.
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
