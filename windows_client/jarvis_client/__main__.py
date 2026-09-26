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


MODES = ("work", "personal")


def _build_token_sources(cfg: config_mod.ClientConfig) -> dict[str, object]:
    """AD16: secretsmanager for the aws profile (nothing written to disk); a plain file for wsl
    (unchanged local behavior). The "file" source is intentionally the SAME token_path for both
    modes: jarvis.api.copy_token_to_windows (app-side, local profile only) still writes a single
    shared token file, so per-mode file tokens are not yet meaningful under that profile -- see
    the deploy-engineer report for this known gap."""
    if cfg.token_source == "secretsmanager":
        return {
            "work": api_mod.SecretsManagerToken(cfg.aws_profile, cfg.token_secret_work),
            "personal": api_mod.SecretsManagerToken(cfg.aws_profile, cfg.token_secret_personal),
        }
    return {mode: api_mod.FileToken(cfg.token_path) for mode in MODES}


def _build_api_clients(cfg: config_mod.ClientConfig) -> dict[str, api_mod.JarvisApi]:
    token_sources = _build_token_sources(cfg)
    urls = {"work": cfg.api_url_work, "personal": cfg.api_url_personal}
    return {mode: api_mod.JarvisApi(urls[mode], token_sources[mode]) for mode in MODES}


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
    log.info("jarvis client starting (profile=%s)", cfg.profile)

    # AD4/G9: one daemon per mode everywhere, so two JarvisApi instances, two SSE consumers. The
    # DualModeApi facade lets flow.VoiceFlow keep calling a single `Api` (mode is just a call
    # argument to it, as before); it dispatches to the right endpoint underneath.
    api_clients = _build_api_clients(cfg)
    dual_api = api_mod.DualModeApi(api_clients)
    speak = lambda text: tts.speak(text, cfg.piper_exe, cfg.piper_voice)  # noqa: E731
    voice_flow = flow_mod.VoiceFlow(dual_api, speak, time.monotonic)

    quit_holder: dict = {}
    tray_app = tray.TrayApp(
        on_open_vault=lambda mode: _open_vault(cfg.vault_work if mode == "work" else cfg.vault_personal),
        on_pending=lambda mode: _show_pending(api_clients[mode], speak, mode),
        on_restart=lambda: _restart_daemon(cfg.wsl_distro, speak),
        on_quit=lambda: quit_holder["fn"](),
        show_restart=(cfg.profile == "wsl"),
    )
    coordinator = tray.TrayStateCoordinator(tray_app)

    stop_event = threading.Event()
    tray.start_health_poller(api_clients, coordinator, speak, stop_event=stop_event)
    for mode, client in api_clients.items():
        threading.Thread(target=notify.run, args=(client, stop_event), daemon=True,
                          name=f"jarvis-notify-{mode}").start()

    # B10's WSL-idle-timeout workaround only applies to the wsl profile: the aws profile's daemons
    # run on the box, not in a local WSL VM that can idle out.
    keepalive = _start_keepalive(cfg.wsl_distro) if cfg.profile == "wsl" else None

    from pynput import keyboard

    work_session = _make_session("work", coordinator, voice_flow, speak, cfg)
    personal_session = _make_session("personal", coordinator, voice_flow, speak, cfg)
    hotkeys = {cfg.hotkey_work: work_session.toggle, cfg.hotkey_personal: personal_session.toggle}
    listener = keyboard.GlobalHotKeys(hotkeys)
    listener.start()

    def _close_everything() -> None:
        if keepalive is not None:
            keepalive.terminate()
        for client in api_clients.values():
            client.close()

    def _quit() -> None:
        stop_event.set()
        listener.stop()
        _close_everything()
        tray_app.stop()

    quit_holder["fn"] = _quit

    try:
        tray_app.run()
    finally:
        stop_event.set()
        _close_everything()


if __name__ == "__main__":
    main()
