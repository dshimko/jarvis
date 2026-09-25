"""tts.speak(): Piper is used when configured and present, with utf-8 explicitly (M1), and any
Piper failure (including a decode error) falls back to SAPI rather than staying silent."""
import subprocess
import sys
import types

import pytest

from jarvis_client import tts


def _fake_winsound():
    module = types.ModuleType("winsound")
    module.PlaySound = lambda *a, **k: None
    module.SND_FILENAME = 0
    return module


def test_piper_is_invoked_with_utf8_encoding_when_configured(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "winsound", _fake_winsound())
    piper_exe = tmp_path / "piper.exe"
    piper_voice = tmp_path / "voice.onnx"
    piper_exe.write_text("x")
    piper_voice.write_text("x")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    tts.speak("hello", str(piper_exe), str(piper_voice))

    assert captured.get("encoding") == "utf-8"


def test_piper_failure_falls_back_to_sapi(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "winsound", _fake_winsound())
    piper_exe = tmp_path / "piper.exe"
    piper_voice = tmp_path / "voice.onnx"
    piper_exe.write_text("x")
    piper_voice.write_text("x")

    def fake_run(cmd, **kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    sapi_calls = []
    monkeypatch.setattr(tts, "_speak_sapi", lambda text: sapi_calls.append(text))

    tts.speak("hello", str(piper_exe), str(piper_voice))

    assert sapi_calls == ["hello"]


def test_missing_piper_paths_go_straight_to_sapi(monkeypatch):
    sapi_calls = []
    monkeypatch.setattr(tts, "_speak_sapi", lambda text: sapi_calls.append(text))

    tts.speak("hello", None, None)

    assert sapi_calls == ["hello"]
