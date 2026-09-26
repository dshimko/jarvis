"""Heartbeat (AD1): `event=heartbeat mode=<mode>` every 300 s. CloudWatch metric filters turn it into
Jarvis/Heartbeat; the daemon itself makes no AWS calls."""
from __future__ import annotations
import logging, threading
from .logsetup import log_event

log = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 300


class Heartbeat(threading.Thread):
    """Beats once at start, then every `interval` seconds until stop()."""

    def __init__(self, mode: str, interval: float = HEARTBEAT_SECONDS):
        super().__init__(daemon=True, name="heartbeat")
        self.mode, self.interval = mode, interval
        self._halt = threading.Event()

    def run(self) -> None:
        while True:
            log_event(log, "heartbeat", mode=self.mode)
            if self._halt.wait(self.interval):
                return

    def stop(self) -> None:
        self._halt.set()
