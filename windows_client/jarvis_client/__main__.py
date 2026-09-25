"""Entry point: wires config, the API client, the voice flow, hardware I/O, the tray icon, and
the SSE toast thread together. Everything Windows-only is imported lazily inside functions."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import api as api_mod
from . import config as config_mod
from . import flow as flow_mod
from . import logging_setup
from . import notify
from . import recording
from . import stt
from . import tray
from . import tts

CREATE_NO_WINDOW = 0x08000000
log = logging.getLogger(__name__)


def _show_pending(api_client: api_mod.JarvisApi, speak, mode: str) -> None:
    items = api_client.outbox(mode)
    if not items:
        speak(f"Nothing pending in {mode}.")
        return
    summary = "; ".join(i.get("first_line") or i.get("id", "") for i in items)
    speak(f"{len(items)} pending in {mode}: {summary}")
    for item in items:
        notify.toast(f"Jarvis ({mode}): pending", item.get("first_line", ""),
                     item.get("obsidian_uri"))


def _open_vault(vault_name: str) -> None:
    import os

    os.startfile(f"obsidian://open?vault={vault_name}")


def _restart_daemon(wsl_distro: str, speak: Callable[[str], None]) -> None:
    """M4: the same login-shell form as start-jarvis.ps1 (systemctl --user needs
    XDG_RUNTIME_DIR, which a bare `wsl.exe -e systemctl` won't have -- B11), with the return code
    checked and a failure reported (never silently swallowed)."""
    import subprocess

    cmd = ["wsl.exe", "-d", wsl_distro, "-e", "sh", "-lc",
           "export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user restart jarvis"]
    try:
        result = subprocess.run(cmd, creationflags=CREATE_NO_WINDOW, capture_output=True,
                                 timeout=30, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("restart daemon failed to launch: %s", type(exc).__name__)
        speak("Could not restart Jarvis.")
        return
    if result.returncode != 0:
        log.warning("restart daemon exited with code %s", result.returncode)
        speak("Could not restart Jarvis.")


def _start_keepalive(wsl_distro: str):
    """B10: keeps one hidden `wsl.exe ... sleep infinity` child alive so the WSL VM doesn't idle
    out and kill the daemon. Terminated on Quit."""
    import subprocess

    return subprocess.Popen(["wsl.exe", "-d", wsl_distro, "-e", "sleep", "infinity"],
                             creationflags=CREATE_NO_WINDOW)


def _make_session(mode: str, coordinator: tray.TrayStateCoordinator, voice_flow, speak,
                   cfg: config_mod.ClientConfig) -> recording.RecordingSession:
    return recording.RecordingSession(
        mode=mode, recorder=stt.Recorder(cfg.sample_rate), coordinator=coordinator,
        voice_flow=voice_flow, speak=speak, transcribe=stt.transcribe,
        whisper_exe=cfg.whisper_exe, whisper_model=cfg.whisper_model,
    )


def main() -> None:
    cfg = config_mod.load_config()
    logging_setup.setup_logging(str(config_mod.jarvis_dir() / "client.log"))
    log.info("jarvis client starting")

    api_client = api_mod.JarvisApi(cfg.api_url, cfg.token_path)
    speak = lambda text: tts.speak(text, cfg.piper_exe, cfg.piper_voice)  # noqa: E731
    voice_flow = flow_mod.VoiceFlow(api_client, speak, time.monotonic)

    quit_holder: dict = {}
    tray_app = tray.TrayApp(
        on_open_vault=lambda mode: _open_vault(cfg.vault_work if mode == "work" else cfg.vault_personal),
        on_pending=lambda mode: _show_pending(api_client, speak, mode),
        on_restart=lambda: _restart_daemon(cfg.wsl_distro, speak),
        on_quit=lambda: quit_holder["fn"](),
    )
    coordinator = tray.TrayStateCoordinator(tray_app)

    stop_event = threading.Event()
    tray.start_health_poller(api_client, coordinator, speak, stop_event=stop_event)
    threading.Thread(target=notify.run, args=(api_client, stop_event), daemon=True,
                      name="jarvis-notify").start()

    keepalive = _start_keepalive(cfg.wsl_distro)

    from pynput import keyboard

    work_session = _make_session("work", coordinator, voice_flow, speak, cfg)
    personal_session = _make_session("personal", coordinator, voice_flow, speak, cfg)
    hotkeys = {cfg.hotkey_work: work_session.toggle, cfg.hotkey_personal: personal_session.toggle}
    listener = keyboard.GlobalHotKeys(hotkeys)
    listener.start()

    def _quit() -> None:
        stop_event.set()
        listener.stop()
        keepalive.terminate()
        api_client.close()
        tray_app.stop()

    quit_holder["fn"] = _quit

    try:
        tray_app.run()
    finally:
        stop_event.set()
        keepalive.terminate()
        api_client.close()


if __name__ == "__main__":
    main()
