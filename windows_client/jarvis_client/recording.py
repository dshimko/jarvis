"""Hotkey toggle -> record -> transcribe -> flow orchestration, split out of __main__.py so it is
unit-testable with fake recorder/coordinator/flow collaborators -- no real mic, tray, or network
needed. `winsound` (the start beep) is imported lazily inside the default beep function.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import stt
from .api import ApiError

log = logging.getLogger(__name__)


def _default_beep() -> None:
    import winsound

    winsound.MessageBeep()


def _default_thread_factory(target: Callable, args: tuple, name: str) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    thread.start()
    return thread


class RecordingSession:
    """One instance per hotkey mode. `toggle()` is the hotkey callback: press to start, press
    again to stop, transcribe, and hand the text to `voice_flow`. Never lets an exception escape
    `toggle()` (M3): pynput's GlobalHotKeys listener thread must survive a mic error."""

    def __init__(self, mode: str, recorder, coordinator, voice_flow, speak: Callable[[str], None],
                 transcribe: Callable[..., str], whisper_exe: str, whisper_model: str,
                 beep: Callable[[], None] | None = None, thread_factory: Callable | None = None,
                 clock: Callable[[], float] | None = None):
        self.mode = mode
        self._recorder = recorder
        self._coordinator = coordinator
        self._flow = voice_flow
        self._speak = speak
        self._transcribe = transcribe
        self._whisper_exe = whisper_exe
        self._whisper_model = whisper_model
        self._beep = beep or _default_beep
        self._thread_factory = thread_factory or _default_thread_factory
        self._clock = clock or time.monotonic
        self._recording_started_at: float | None = None

    def toggle(self) -> None:
        try:
            if not self._recorder.active:
                self._start()
            else:
                self._stop_and_process()
        except Exception as exc:
            log.warning("recorder toggle failed mode=%s error=%s", self.mode, type(exc).__name__)
            self._recorder.active = False
            self._coordinator.set_recording(self.mode, False)
            self._safe_speak("Microphone error")

    def _start(self) -> None:
        # Stamped before opening the mic (never after), so it's always <= the real start time --
        # erring toward rejecting a borderline confirm rather than accepting a stale one (M2).
        self._recording_started_at = self._clock()
        self._coordinator.set_recording(self.mode, True)
        self._beep()
        self._recorder.start()

    def _stop_and_process(self) -> None:
        wav = self._recorder.stop()
        started_at = self._recording_started_at
        try:
            self._coordinator.set_recording(self.mode, False)
            self._coordinator.set_transient("thinking")
            self._thread_factory(target=self._process, args=(wav, started_at),
                                  name=f"jarvis-utterance-{self.mode}")
        except Exception:
            # The WAV is already on disk (recorder.stop() wrote it); if we never manage to hand
            # it off to _process (e.g. the thread fails to start), nothing else will ever call
            # transcribe()'s own cleanup, so it would otherwise be orphaned in the temp dir.
            if wav is not None:
                stt.cleanup_wav(wav)
            raise

    def _process(self, wav, started_at: float | None) -> None:
        try:
            if wav is None:
                return
            text = self._transcribe(wav, self._whisper_exe, self._whisper_model)
            if text:
                self._flow.on_utterance(text, self.mode, recording_started_at=started_at)
        except ApiError as exc:
            # H2: never log detail/text, only the safe, structured fields.
            log.warning("utterance processing failed mode=%s error=%s status=%s",
                        self.mode, type(exc).__name__, exc.status_code)
        except Exception as exc:
            log.warning("utterance processing failed mode=%s error=%s", self.mode,
                        type(exc).__name__)
        finally:
            self._coordinator.rest()

    def _safe_speak(self, message: str) -> None:
        try:
            self._speak(message)
        except Exception as exc:
            log.warning("failed to speak an error message: %s", type(exc).__name__)
