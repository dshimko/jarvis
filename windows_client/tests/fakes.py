"""Shared test doubles for jarvis_client.flow tests."""
from __future__ import annotations

from dataclasses import dataclass, field


class FakeClock:
    """Injectable monotonic clock: on_utterance never calls time.sleep, tests just move it."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Recorder:
    """Captures every speak() call, in order, verbatim."""

    lines: list = field(default_factory=list)

    def __call__(self, text: str) -> None:
        self.lines.append(text)


class FakeApi:
    """Records calls it receives and returns scripted responses.

    - `utterance_responses`: list of dicts returned in order by successive .utterance() calls
      (last one repeats once exhausted).
    - `outbox_items`: list of item dicts returned by .outbox() for any mode.
    - `approve_result` / `approve_error`: what .approve() returns or raises.
    """

    def __init__(self, utterance_responses=None, outbox_items=None, approve_result=None,
                 approve_error=None):
        self.utterance_responses = list(utterance_responses or
                                         [{"reply": "ok", "mode_used": None, "needs_mode": False}])
        self._utterance_index = 0
        self.outbox_items = outbox_items or []
        self.approve_result = approve_result if approve_result is not None else {"result": "Sent.",
                                                                                  "sent": True,
                                                                                  "blocked": None}
        self.approve_error = approve_error
        self.utterance_calls: list[tuple] = []
        self.outbox_calls: list[str] = []
        self.approve_calls: list[tuple] = []

    def utterance(self, text: str, mode: str, mode_confirmed: bool = False) -> dict:
        self.utterance_calls.append((text, mode, mode_confirmed))
        idx = min(self._utterance_index, len(self.utterance_responses) - 1)
        self._utterance_index += 1
        return self.utterance_responses[idx]

    def outbox(self, mode: str) -> list:
        self.outbox_calls.append(mode)
        return self.outbox_items

    def approve(self, mode: str, item_id: str, body_sha256: str, readback_sha256: str,
                reconfirm: bool = False) -> dict:
        self.approve_calls.append((mode, item_id, body_sha256, readback_sha256, reconfirm))
        if self.approve_error is not None:
            raise self.approve_error
        return self.approve_result
