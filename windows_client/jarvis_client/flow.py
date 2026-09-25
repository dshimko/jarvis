"""Pure voice state machine: utterance round-trips, mode confirmation, and the approve/confirm
flow. Never imports hardware or Windows modules, so it unit-tests on any platform.

`api` needs `.utterance(text, mode, mode_confirmed) -> dict`, `.outbox(mode) -> list[dict]`, and
`.approve(mode, id, body_sha256, readback_sha256, reconfirm) -> dict` (see api.py; any exception
raised by `.approve` is treated as a failed send and mapped to a friendly message via
`getattr(exc, "status_code", None)`, so flow.py need not import api.py's exception type).
`speak` is `Callable[[str], None]`. `clock` is `Callable[[], float]` (monotonic seconds).
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

CONFIRM_TIMEOUT_SECONDS = 60.0
MODE_HINT = {"work": "Press Ctrl+Alt+W for work", "personal": "Press Ctrl+Alt+P for personal"}
CONFIRM_WORDS = {"yes", "continue", "confirm"}
NOTHING_SENT = "Nothing was sent."
TRAILING_PUNCT = re.compile(r"^[\s.,!?;:'\"]+|[\s.,!?;:'\"]+$")
APPROVE_RE = re.compile(r"^(approve|send)(?:\s+(.+))?$")
FRIENDLY_ERRORS = {
    404: "That item isn't there anymore.",
    409: "That item changed, try again.",
    400: "That request wasn't valid.",
}


class Api(Protocol):
    def utterance(self, text: str, mode: str, mode_confirmed: bool = False) -> dict: ...
    def outbox(self, mode: str) -> list[dict]: ...
    def approve(self, mode: str, item_id: str, body_sha256: str, readback_sha256: str,
                reconfirm: bool = False) -> dict: ...


def normalize(text: str) -> str:
    """Lowercase, strip, drop leading/trailing punctuation, collapse whitespace (whisper outputs
    things like "Confirm.")."""
    collapsed = " ".join((text or "").split())
    return TRAILING_PUNCT.sub("", collapsed).lower()


def _slug(text: str) -> str:
    """Spaces/punctuation removed, lowercase -- for matching a spoken token against an item id."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def other_mode(mode: str) -> str:
    return "personal" if mode == "work" else "work"


@dataclass(frozen=True)
class _AwaitingMode:
    original_text: str


@dataclass(frozen=True)
class _AwaitingConfirm:
    mode: str
    item_id: str
    body_sha256: str
    readback_sha256: str
    tone_flagged: bool
    deadline: float
    prompt_finished_at: float


class VoiceFlow:
    def __init__(self, api: Api, speak: Callable[[str], None], clock: Callable[[], float]):
        self._api = api
        self._speak = speak
        self._clock = clock
        self._awaiting_mode: _AwaitingMode | None = None
        self._awaiting_confirm: _AwaitingConfirm | None = None
        self._lock = threading.Lock()

    def on_utterance(self, text: str, mode: str, recording_started_at: float | None = None) -> None:
        """`mode` is always the hotkey that was pressed for THIS recording (voice mode is
        hotkey-bound; a spoken prefix never overrides it). `recording_started_at` (monotonic
        seconds, same clock as `clock`) is when the mic actually started recording this
        utterance; None means "now" (the default used by tests that don't care about ordering).

        Serialized with a lock: two hotkey threads can call this concurrently (e.g. a stray
        confirm from the other mode racing the real one), and only one may observe+consume
        `_awaiting_confirm` -- so at most one /approve is ever posted per prompt (M2)."""
        with self._lock:
            started_at = self._clock() if recording_started_at is None else recording_started_at
            if self._awaiting_confirm is not None:
                self._handle_confirm(text, mode, started_at)
                return
            if self._awaiting_mode is not None:
                self._handle_mode_answer(text, mode)
                return
            self._handle_new(text, mode)

    # -- fresh utterance -----------------------------------------------------------------

    def _handle_new(self, text: str, mode: str) -> None:
        norm = normalize(text)
        match = APPROVE_RE.match(norm)
        is_bare_send = match and match.group(1) == "send" and not match.group(2)
        if match and not is_bare_send:
            self._handle_approve_intent(mode, match.group(2))
            return
        self._do_utterance(text, mode, mode_confirmed=False)

    def _do_utterance(self, text: str, mode: str, mode_confirmed: bool) -> None:
        result = self._api.utterance(text, mode, mode_confirmed)
        self._speak(result.get("reply", ""))
        self._awaiting_mode = _AwaitingMode(original_text=text) if result.get("needs_mode") else None

    # -- needs_mode round trip ------------------------------------------------------------

    def _handle_mode_answer(self, text: str, mode: str) -> None:
        pending = self._awaiting_mode
        norm = normalize(text)
        if norm == mode or norm in CONFIRM_WORDS:
            self._awaiting_mode = None
            self._do_utterance(pending.original_text, mode, mode_confirmed=True)
            return
        if norm == other_mode(mode):
            self._speak(MODE_HINT[other_mode(mode)])
            return  # stays in _awaiting_mode, waiting for the matching hotkey
        self._awaiting_mode = None
        self._handle_new(text, mode)

    # -- approve intent --------------------------------------------------------------------

    def _handle_approve_intent(self, mode: str, spoken: str | None) -> None:
        items = self._api.outbox(mode)
        item = self._match_item(items, spoken)
        if item is None:
            slugs = "; ".join(i.get("id", "") for i in items)
            self._speak(f"Which one? You have {len(items)}: {slugs}")
            return
        self._start_confirm(mode, item)

    @staticmethod
    def _match_item(items: list[dict], spoken: str | None) -> dict | None:
        if not spoken:
            return items[0] if len(items) == 1 else None
        token = _slug(spoken)
        if len(token) < 3:
            return None
        matches = [i for i in items if token in _slug(i.get("id", ""))]
        return matches[0] if len(matches) == 1 else None

    def _start_confirm(self, mode: str, item: dict) -> None:
        readback = item.get("readback", "")
        if sha256_hex(readback) != item.get("readback_sha256"):
            self._speak("That item changed, try again")
            return
        self._speak(readback)
        tone_flagged = bool(item.get("tone_flagged"))
        self._speak("This one was tone flagged. Say confirm reconfirm to send."
                     if tone_flagged else "Say confirm to send.")
        self._awaiting_confirm = _AwaitingConfirm(
            mode=mode, item_id=item.get("id", ""), body_sha256=item.get("body_sha256", ""),
            readback_sha256=sha256_hex(readback), tone_flagged=tone_flagged,
            deadline=self._clock() + CONFIRM_TIMEOUT_SECONDS,
            prompt_finished_at=self._clock(),
        )

    # -- awaiting_confirm --------------------------------------------------------------------

    def _handle_confirm(self, text: str, mode: str, started_at: float) -> None:
        pending = self._awaiting_confirm
        self._awaiting_confirm = None
        if mode != pending.mode or self._clock() >= pending.deadline:
            self._speak(NOTHING_SENT)
            return
        if started_at < pending.prompt_finished_at:
            # M2: this utterance's recording began before we finished speaking the confirm
            # prompt, so the user could not have heard it yet -- any "confirm" in it is stale
            # audio, not a real response.
            self._speak(NOTHING_SENT)
            return
        expected = "confirm reconfirm" if pending.tone_flagged else "confirm"
        if normalize(text) != expected:
            self._speak(NOTHING_SENT)
            return
        self._send_approval(pending)

    def _send_approval(self, pending: _AwaitingConfirm) -> None:
        try:
            result = self._api.approve(pending.mode, pending.item_id, pending.body_sha256,
                                        pending.readback_sha256, reconfirm=pending.tone_flagged)
        except Exception as exc:  # noqa: BLE001 -- any transport/HTTP error becomes a spoken message
            status = getattr(exc, "status_code", None)
            self._speak(FRIENDLY_ERRORS.get(status, "Something went wrong."))
            return
        self._speak(result.get("result", ""))
