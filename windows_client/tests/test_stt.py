"""stt.transcribe(): parses whisper-cli stdout and always deletes the temp WAV (and its private
temp dir), even when the subprocess call raises."""
import subprocess
from pathlib import Path

import pytest

from jarvis_client import stt


def _make_wav(tmp_path) -> Path:
    wav_dir = tmp_path / "jarvis-rec-abc"
    wav_dir.mkdir()
    wav_path = wav_dir / "utterance.wav"
    wav_path.write_bytes(b"fake-wav-bytes")
    return wav_path


def test_transcribe_parses_stdout_and_deletes_the_wav(tmp_path, monkeypatch):
    wav_path = _make_wav(tmp_path)

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "whisper-cli.exe"
        assert str(wav_path) in cmd
        assert kwargs.get("encoding") == "utf-8"  # M1: explicit utf-8, not the locale codepage
        return subprocess.CompletedProcess(cmd, 0, stdout="  hello   world  \n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    text = stt.transcribe(wav_path, "whisper-cli.exe", "ggml-base.en.bin")

    assert text == "hello world"
    assert not wav_path.exists()
    assert not wav_path.parent.exists()


def test_transcribe_returns_empty_string_and_still_deletes_wav_on_decode_error(tmp_path, monkeypatch):
    wav_path = _make_wav(tmp_path)

    def fake_run(cmd, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")

    monkeypatch.setattr(subprocess, "run", fake_run)

    text = stt.transcribe(wav_path, "whisper-cli.exe", "ggml-base.en.bin")

    assert text == ""
    assert not wav_path.exists()


def test_transcribe_still_deletes_the_wav_when_the_subprocess_raises(tmp_path, monkeypatch):
    wav_path = _make_wav(tmp_path)

    def fake_run(cmd, **kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.SubprocessError):
        stt.transcribe(wav_path, "whisper-cli.exe", "ggml-base.en.bin")

    assert not wav_path.exists()
