"""Event bus for the SSE stream, plus the outbox poller (polling, never inotify: B6)."""
from __future__ import annotations
import asyncio, logging, threading
from pathlib import Path
from typing import Callable
from . import outbox, paths
from .logsetup import log_event
from .modes import Mode

log = logging.getLogger(__name__)
QUEUE_MAX = 100
POLL_SECONDS = 5.0
TYPES = {"pending_new", "item_sent", "item_blocked", "schedule_done"}


class EventBus:
    """publish() is safe from any thread; events fan out to per-subscriber bounded asyncio queues (SSE) and to
    synchronous listeners (Telegram push), which run in the publishing thread and must not block for long."""

    def __init__(self, maxsize: int = QUEUE_MAX):
        self._maxsize = maxsize
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()
        self._listeners: tuple[Callable[[str, dict], None], ...] = ()
        self._lock = threading.Lock()

    def add_listener(self, fn: Callable[[str, dict], None]) -> None:
        with self._lock:
            self._listeners = (*self._listeners, fn)

    def _call_listeners(self, kind: str, data: dict) -> None:
        for fn in self._listeners:
            try:
                fn(kind, dict(data))
            except Exception as e:
                log_event(log, "bus_listener_error", logging.ERROR, error_class=type(e).__name__)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, kind: str, data: dict) -> None:
        if kind not in TYPES:
            raise ValueError(f"unknown event type {kind}")
        self._call_listeners(kind, data)
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # nobody can be listening yet
        event = {"type": kind, "data": dict(data)}
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:
            log.debug("event loop closed; dropped %s", kind)

    def _fanout(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            if q.full():                 # slow consumer: drop its oldest event, keep the newest
                q.get_nowait()
            q.put_nowait(event)


def outbox_listener(bus: EventBus) -> Callable[[str, str, str, str | None], None]:
    """Adapter so outbox.execute publishes item_sent / item_blocked."""
    def listener(kind: str, mode: str, item_id: str, reason: str | None) -> None:
        bus.publish(kind, {"mode": mode, "id": item_id, **({"reason": reason} if reason else {})})
    return listener


def _first_line(body: str) -> str:
    return next((line.strip() for line in body.splitlines() if line.strip()), "")


class OutboxPoller(threading.Thread):
    """Every POLL_SECONDS, publish pending_new for pending items not seen before. The first scan seeds silently."""

    def __init__(self, modes: dict[str, Mode], bus: EventBus, interval: float = POLL_SECONDS):
        super().__init__(daemon=True, name="outbox-poller")
        self.modes, self.bus, self.interval = modes, bus, interval
        self._halt = threading.Event()
        self._known: frozenset = frozenset()

    def _scan(self) -> dict[tuple[str, str], tuple[str, Path]]:
        return {(name, str(meta.get("id"))): (body, path) for name, mode in self.modes.items()
                for meta, body, path in outbox.pending_items(mode)}

    def seed(self) -> None:
        self._known = frozenset(self._scan())

    def poll_once(self) -> list[tuple[str, str]]:
        current = self._scan()
        new = [key for key in current if key not in self._known]
        for name, item_id in new:
            body, path = current[(name, item_id)]
            self.bus.publish("pending_new", {
                "mode": name, "id": item_id, "first_line": _first_line(body),
                "obsidian_uri": paths.obsidian_uri(self.modes[name].vault, f"outbox/{path.name}",
                                                   self.modes[name].obsidian_vault_name)})
        self._known = frozenset(current)
        return new

    def run(self) -> None:
        self.seed()
        while not self._halt.wait(self.interval):
            try:
                self.poll_once()
            except Exception as e:
                log_event(log, "outbox_poll_error", logging.ERROR, error_class=type(e).__name__)

    def stop(self) -> None:
        self._halt.set()
