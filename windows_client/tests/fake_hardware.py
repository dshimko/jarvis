"""Shared fake recorder/coordinator collaborators for recording.RecordingSession tests."""
from __future__ import annotations

from dataclasses import dataclass, field


class FakeRecorder:
    def __init__(self, wav_to_return=None, start_error: Exception | None = None):
        self.active = False
        self._wav_to_return = wav_to_return
        self._start_error = start_error
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error
        self.active = True

    def stop(self):
        self.stop_calls += 1
        self.active = False
        return self._wav_to_return


@dataclass
class FakeCoordinator:
    recording_calls: list = field(default_factory=list)
    transient_calls: list = field(default_factory=list)
    rest_calls: int = 0

    def set_recording(self, mode: str, active: bool) -> None:
        self.recording_calls.append((mode, active))

    def set_transient(self, state: str) -> None:
        self.transient_calls.append(state)

    def rest(self) -> None:
        self.rest_calls += 1


@dataclass
class FakeVoiceFlow:
    """`on_utterance_error`, if set, is raised instead of recording the call."""

    on_utterance_error: Exception | None = None
    calls: list = field(default_factory=list)

    def on_utterance(self, text: str, mode: str, recording_started_at=None) -> None:
        self.calls.append((text, mode, recording_started_at))
        if self.on_utterance_error is not None:
            raise self.on_utterance_error


def run_now(target, args, name):
    """A synchronous thread_factory: runs target(*args) immediately in the calling thread, so
    tests don't need to sleep/join."""
    target(*args)
