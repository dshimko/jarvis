"""Reading the `status` word from an ofw-mcp tool result (AD35). Content-free by construction.

Every ofw-mcp result is one JSON object with a `status`. It is read from `structuredContent` when present,
else from the first text content block parsed as JSON. Only an exact AD35 vocabulary word is accepted as a
status, so nothing but a fixed word (or None) ever reaches an outbox item, a log line, or a reply.
"""
from __future__ import annotations
import json
from dataclasses import dataclass

STATUS_VOCABULARY = frozenset({"ok", "rate_limited", "breaker_open", "challenge_required", "layout_changed",
                               "writes_disabled", "sent", "sent_unverified", "duplicate", "error"})   # AD35
NO_STATUS = "no status"
SENT = "sent"
SENT_WITH_NOTE = frozenset({"sent_unverified", "duplicate"})


@dataclass(frozen=True)
class SendResult:
    sent: bool
    note: str | None = None        # sent: sent_unverified / duplicate; failed: the status word or NO_STATUS


def _first_text_json(result):
    content = getattr(result, "content", None)
    if not isinstance(content, (list, tuple)) or not content:
        return None
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def payload_of(result) -> dict:
    """The result's JSON object (structuredContent, else the first text block), or {}. Callers whitelist
    fields from it; it is never logged or returned whole."""
    data = getattr(result, "structuredContent", None)
    if not isinstance(data, dict) or not data:
        data = _first_text_json(result)
    return data if isinstance(data, dict) else {}


def status_of(result) -> str | None:
    """The result's status word, or None when missing or not exactly one of the AD35 vocabulary words."""
    status = payload_of(result).get("status")
    return status if isinstance(status, str) and status in STATUS_VOCABULARY else None


def classify_send(result) -> SendResult:
    """Executor mapping for server ofw: sent -> sent; sent_unverified/duplicate -> sent with a note;
    anything else (including an isError result) -> failed with the status word or NO_STATUS."""
    status = status_of(result)
    if getattr(result, "isError", False):
        return SendResult(False, status if status and status != SENT else NO_STATUS)
    if status == SENT:
        return SendResult(True)
    if status in SENT_WITH_NOTE:
        return SendResult(True, status)
    return SendResult(False, status or NO_STATUS)
