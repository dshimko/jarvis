"""Tray icon (Pillow-drawn circles), menu, and the /health poller that drives the grey state.

pystray/PIL are imported lazily inside functions so this module can be imported on any platform;
only `run()` and `_build_icon()` actually need a display.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

STATE_COLORS = {
    "idle": (46, 204, 113),               # green
    "recording-work": (52, 120, 246),     # blue
    "recording-personal": (245, 166, 35), # amber
    "thinking": (155, 89, 182),           # purple
    "daemon-unreachable": (149, 149, 149),# grey
}
HEALTH_POLL_SECONDS = 10.0


def _draw_icon(color: tuple[int, int, int]):
    from PIL import Image, ImageDraw

    size, pad = 64, 4
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([pad, pad, size - pad, size - pad], fill=color + (255,))
    return image


class TrayApp:
    """Owns the pystray.Icon. `on_open_vault(mode)`, `on_pending(mode)`, `on_restart()`, and
    `on_quit()` are plain callbacks supplied by __main__.py so this module stays UI-only."""

    def __init__(self, on_open_vault: Callable[[str], None], on_pending: Callable[[str], None],
                 on_restart: Callable[[], None], on_quit: Callable[[], None]):
        self._on_open_vault = on_open_vault
        self._on_pending = on_pending
        self._on_restart = on_restart
        self._on_quit = on_quit
        self._icon = None
        self._state = "idle"
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def set_state(self, state: str) -> None:
        with self._lock:
            self._state = state
            if self._icon is not None:
                self._icon.icon = _draw_icon(STATE_COLORS.get(state, STATE_COLORS["idle"]))
                self._icon.title = f"Jarvis ({state})"

    def _build_menu(self):
        import pystray

        return pystray.Menu(
            pystray.MenuItem("Open Work Vault", lambda: self._on_open_vault("work")),
            pystray.MenuItem("Open Personal Vault", lambda: self._on_open_vault("personal")),
            pystray.MenuItem("Pending (work)", lambda: self._on_pending("work")),
            pystray.MenuItem("Pending (personal)", lambda: self._on_pending("personal")),
            pystray.MenuItem("Restart Daemon", lambda: self._on_restart()),
            pystray.MenuItem("Quit", lambda: self._on_quit()),
        )

    def run(self) -> None:
        """Blocks until stop() is called (or the user quits from the menu)."""
        import pystray

        self._icon = pystray.Icon("jarvis", _draw_icon(STATE_COLORS["idle"]), "Jarvis (idle)",
                                   self._build_menu())
        self._icon.run()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()


class TrayStateCoordinator:
    """Central authority for the tray icon's "resting" state -- what to show once a transient
    action (a recording, "thinking", a mic error) finishes -- so one source never clobbers
    another. Without this, `_process`'s `finally: set_state("idle")` could stomp on
    "daemon-unreachable" (still true) or on the OTHER hotkey's recording still in progress."""

    def __init__(self, tray_app: TrayApp):
        self._tray = tray_app
        self._unreachable = False
        self._recording_modes: set[str] = set()
        self._transient: str | None = None
        self._lock = threading.Lock()

    def set_unreachable(self, unreachable: bool) -> None:
        """Only acts (and re-applies the resting state) on an actual change -- a health poll
        fires every ~10s regardless of outcome, and re-applying on every healthy poll would keep
        clobbering "thinking" back to idle during a long-running /utterance."""
        with self._lock:
            if self._unreachable == unreachable:
                return
            self._unreachable = unreachable
        self._apply()

    def set_recording(self, mode: str, active: bool) -> None:
        with self._lock:
            if active:
                self._recording_modes.add(mode)
            else:
                self._recording_modes.discard(mode)
        self._apply()

    def set_transient(self, state: str) -> None:
        """"thinking" (or any other transient state) is shown immediately, and stays the resting
        state -- surviving an unrelated set_unreachable(False)/set_recording() update from
        another mode -- until rest() is called."""
        with self._lock:
            self._transient = state
        self._apply()

    def rest(self) -> None:
        """Called once a transient action is done; (re)applies whichever resting state -- grey,
        an in-progress recording of the other mode, or idle -- is still true."""
        with self._lock:
            self._transient = None
        self._apply()

    def _apply(self) -> None:
        """Precedence: daemon-unreachable > an in-progress recording > "thinking" > idle."""
        with self._lock:
            if self._unreachable:
                self._tray.set_state("daemon-unreachable")
                return
            for mode in self._recording_modes:
                self._tray.set_state(f"recording-{mode}")
                return
            if self._transient is not None:
                self._tray.set_state(self._transient)
                return
            self._tray.set_state("idle")


def start_health_poller(api, coordinator: TrayStateCoordinator, speak: Callable[[str], None],
                         interval: float = HEALTH_POLL_SECONDS,
                         stop_event: threading.Event | None = None) -> threading.Thread:
    """Polls /health every `interval` seconds. Speaks "Jarvis is offline" once, on the transition
    into daemon-unreachable, not on every failed poll."""
    stop_event = stop_event or threading.Event()

    def _poll() -> None:
        was_unreachable = False
        while not stop_event.is_set():
            try:
                api.health()
                was_unreachable = False
                coordinator.set_unreachable(False)
            except Exception:
                if not was_unreachable:
                    speak("Jarvis is offline")
                was_unreachable = True
                coordinator.set_unreachable(True)
            stop_event.wait(interval)

    thread = threading.Thread(target=_poll, daemon=True, name="jarvis-health-poller")
    thread.start()
    return thread
