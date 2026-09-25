"""RecordingSession: the hotkey toggle -> transcribe -> flow pipeline, and its error handling
(M3: a mic error never kills the hotkey listener; H2: an ApiError's status/text never leaks into
logs; the LOW "finally clobbers state" fix: `_process` restores the coordinator's resting state
via `rest()` instead of forcing "idle")."""
from pathlib import Path

import httpx
import pytest

from jarvis_client import logging_setup
from jarvis_client.api import ApiError, JarvisApi
from jarvis_client.recording import RecordingSession

from fake_hardware import FakeCoordinator, FakeRecorder, FakeVoiceFlow, run_now


def _session(recorder=None, coordinator=None, voice_flow=None, transcribe=None, clock=None,
             beep=None):
    return RecordingSession(
        mode="work", recorder=recorder or FakeRecorder(), coordinator=coordinator or FakeCoordinator(),
        voice_flow=voice_flow or FakeVoiceFlow(), speak=lambda t: None,
        transcribe=transcribe or (lambda *a, **k: "hello"), whisper_exe="whisper.exe",
        whisper_model="model.bin", beep=beep or (lambda: None), thread_factory=run_now,
        clock=clock or (lambda: 1.0),
    )


def test_toggle_starts_then_stops_and_processes():
    recorder = FakeRecorder(wav_to_return=Path("/tmp/x.wav"))
    coordinator = FakeCoordinator()
    flow = FakeVoiceFlow()
    session = _session(recorder=recorder, coordinator=coordinator, voice_flow=flow)

    session.toggle()  # start
    assert recorder.active is True
    assert coordinator.recording_calls == [("work", True)]

    session.toggle()  # stop -> transcribe -> flow.on_utterance -> rest()

    assert recorder.stop_calls == 1
    assert coordinator.recording_calls[-1] == ("work", False)
    assert coordinator.transient_calls == ["thinking"]
    assert coordinator.rest_calls == 1
    assert flow.calls == [("hello", "work", 1.0)]


def test_no_wav_means_no_transcribe_or_flow_call_but_state_still_rests():
    recorder = FakeRecorder(wav_to_return=None)
    coordinator = FakeCoordinator()
    flow = FakeVoiceFlow()
    calls = []
    session = _session(recorder=recorder, coordinator=coordinator, voice_flow=flow,
                        transcribe=lambda *a, **k: calls.append(1))

    session.toggle()
    session.toggle()

    assert calls == []
    assert flow.calls == []
    assert coordinator.rest_calls == 1


def test_empty_transcript_is_not_sent_to_the_flow():
    session = _session(transcribe=lambda *a, **k: "", voice_flow=(flow := FakeVoiceFlow()))

    session.toggle()
    session.toggle()

    assert flow.calls == []


def test_recorder_start_failure_resets_state_speaks_and_survives():
    """M3: recorder.start() sets .active=True before it can fail (see stt.Recorder.start()), so
    toggle() must reset it back to False on error, along with the tray state, and must not raise
    (the pynput listener thread must survive)."""
    recorder = FakeRecorder(start_error=OSError("no such device"))
    coordinator = FakeCoordinator()
    spoken = []
    session = _session(recorder=recorder, coordinator=coordinator)
    session._speak = spoken.append

    session.toggle()  # must not raise

    assert recorder.active is False
    assert coordinator.recording_calls[-1] == ("work", False)
    assert spoken == ["Microphone error"]


def test_api_error_during_processing_is_logged_without_status_leaking_into_caplog(caplog):
    """H2: only type(exc).__name__ and status_code are logged, never server_detail/text."""
    marker = "XYZZY-SECRET-TRANSCRIPT"
    error = ApiError(422, server_detail=None)
    flow = FakeVoiceFlow(on_utterance_error=error)
    coordinator = FakeCoordinator()
    recorder = FakeRecorder(wav_to_return=Path("/tmp/x.wav"))
    session = _session(recorder=recorder, voice_flow=flow, coordinator=coordinator,
                        transcribe=lambda *a, **k: marker)

    with caplog.at_level("WARNING"):
        session.toggle()
        session.toggle()

    assert coordinator.rest_calls == 1
    assert marker not in caplog.text
    assert "422" in caplog.text
    assert "ApiError" in caplog.text


def test_generic_exception_during_processing_does_not_crash_and_still_rests(caplog):
    flow = FakeVoiceFlow(on_utterance_error=RuntimeError("boom"))
    coordinator = FakeCoordinator()
    recorder = FakeRecorder(wav_to_return=Path("/tmp/x.wav"))
    session = _session(recorder=recorder, voice_flow=flow, coordinator=coordinator)

    with caplog.at_level("WARNING"):
        session.toggle()
        session.toggle()

    assert coordinator.rest_calls == 1
    assert "RuntimeError" in caplog.text


def test_wav_is_deleted_if_something_fails_after_recorder_stop(tmp_path):
    """LOW: recorder.stop() has already written the WAV to disk by the time _stop_and_process
    hands off to a worker thread; if that hand-off itself fails (e.g. the thread fails to
    start), nothing else will ever call transcribe()'s own cleanup, so _stop_and_process must
    clean up the WAV (and its private temp dir) itself."""
    wav_dir = tmp_path / "jarvis-rec-xyz"
    wav_dir.mkdir()
    wav_path = wav_dir / "utterance.wav"
    wav_path.write_bytes(b"fake-wav-bytes")

    recorder = FakeRecorder(wav_to_return=wav_path)
    coordinator = FakeCoordinator()
    session = _session(recorder=recorder, coordinator=coordinator)

    def failing_thread_factory(target, args, name):
        raise RuntimeError("could not start thread")

    session._thread_factory = failing_thread_factory

    session.toggle()  # start
    session.toggle()  # stop -> hand-off fails -> caught by toggle()'s own except, must not raise

    assert not wav_path.exists()
    assert not wav_path.parent.exists()


def test_no_transcript_or_secret_text_reaches_the_log_file(tmp_path):
    """H2, end-to-end: a 422 response body containing a marker (as a validation error would echo
    the submitted text) goes through the real _raise_for_status, and the resulting ApiError goes
    through the real _process, with file logging configured into tmp_path. The marker must never
    reach disk -- neither via the exception nor via the transcript itself."""
    marker = "XYZZY-SECRET-UTTERANCE-3f9a"
    logging_setup.setup_logging(str(tmp_path / "client.log"))

    resp = httpx.Response(422, json={"detail": f"string too long: {marker}"})
    with pytest.raises(ApiError) as exc_info:
        JarvisApi._raise_for_status(resp)
    error = exc_info.value

    flow = FakeVoiceFlow(on_utterance_error=error)
    recorder = FakeRecorder(wav_to_return=Path("/tmp/x.wav"))
    session = _session(recorder=recorder, voice_flow=flow, transcribe=lambda *a, **k: marker)

    session.toggle()
    session.toggle()

    assert flow.calls, "sanity: the flow must actually have been invoked"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert marker not in path.read_text(encoding="utf-8", errors="ignore")
