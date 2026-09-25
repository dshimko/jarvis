"""Toast notifications driven by the /events SSE stream.

win11toast is imported lazily inside `toast()` so this module (and its event-routing logic) can
be imported and unit-tested on any platform.
"""
from __future__ import annotations

import logging
import threading

from .api import JarvisApi, SSEEvent

log = logging.getLogger(__name__)


def toast(title: str, body: str, obsidian_uri: str | None = None) -> None:
    """A single 'Open in Obsidian' button when a uri is given -- by design, never an approve
    button (voice approval only happens through the read-back + confirm flow)."""
    from win11toast import notify

    kwargs = {"title": title, "body": body}
    if obsidian_uri:
        kwargs["button"] = {"activationType": "protocol", "arguments": obsidian_uri,
                             "content": "Open in Obsidian"}
    notify(**kwargs)


def handle_event(evt: SSEEvent) -> None:
    """Tolerates unknown fields and event types (e.g. item_sent, which this client doesn't toast)."""
    data = evt.data or {}
    mode = data.get("mode", "")
    if evt.event == "pending_new":
        toast(f"Jarvis ({mode}): new draft", data.get("first_line", ""), data.get("obsidian_uri"))
    elif evt.event == "item_blocked":
        toast(f"Jarvis ({mode}): blocked", data.get("reason", ""))
    elif evt.event == "schedule_done":
        status = "done" if data.get("ok") else "failed"
        toast("Jarvis", f"{data.get('job', 'job')}: {status}")
    else:
        log.info("unhandled event kind=%s", evt.event)


def run(api_client: JarvisApi, stop_event: threading.Event | None = None) -> None:
    """Consumes api_client.iter_events() forever (it already reconnects on its own)."""
    for evt in api_client.iter_events():
        if stop_event is not None and stop_event.is_set():
            return
        try:
            handle_event(evt)
        except Exception:
            log.exception("failed to handle event kind=%s", evt.event)
