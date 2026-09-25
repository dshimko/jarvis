"""Speech-to-text: 16 kHz mono int16 recording via sounddevice, transcribed by whisper.cpp.

sounddevice/soundfile/numpy are imported lazily inside functions (not at module import time) so
this module can still be imported -- and its pure parts tested -- on a machine without them.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
WHISPER_TIMEOUT_SECONDS = 120


class Recorder:
    """One instance per hotkey mode; start()/stop() toggle a single in-memory recording."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.active = False
        self._frames: list = []
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        self._frames = []
        self.active = True
        self._stream = sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="int16",
                                       callback=lambda data, *_: self._frames.append(data.copy()))
        self._stream.start()

    def stop(self) -> Path | None:
        """Stops the stream and writes a WAV into a fresh private temp dir. Returns None if
        nothing was recorded (e.g. an instant press-release)."""
        import numpy as np
        import soundfile as sf

        self.active = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._frames:
            return None
        tmp_dir = Path(tempfile.mkdtemp(prefix="jarvis-rec-"))
        wav_path = tmp_dir / "utterance.wav"
        sf.write(wav_path, np.concatenate(self._frames), self.sample_rate)
        return wav_path


def transcribe(wav_path: Path, whisper_exe: str, whisper_model: str) -> str:
    """Runs whisper-cli.exe on wav_path (hidden window on Windows) and returns the parsed
    transcript. Always deletes wav_path and its private temp dir, even on failure. Decodes
    stdout as UTF-8 explicitly (not the console's locale codepage); a decode failure is logged
    (never the raw bytes) and treated as an empty transcript rather than crashing the caller."""
    try:
        kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run(
            [whisper_exe, "-m", whisper_model, "-f", str(wav_path), "-nt", "-np", "-l", "en"],
            capture_output=True, encoding="utf-8", timeout=WHISPER_TIMEOUT_SECONDS, **kwargs,
        )
        return " ".join(result.stdout.split()).strip()
    except UnicodeError:
        log.warning("whisper-cli output could not be decoded as utf-8")
        return ""
    finally:
        cleanup_wav(wav_path)


def cleanup_wav(wav_path: Path) -> None:
    """Deletes wav_path and its private temp dir (best-effort). Public so recording.py can reuse
    it to clean up a WAV that recorder.stop() already wrote when something fails before
    transcribe() ever gets a chance to run (e.g. the processing thread fails to start)."""
    try:
        wav_path.unlink(missing_ok=True)
        wav_path.parent.rmdir()
    except OSError:
        log.warning("could not remove temp recording dir %s", wav_path.parent)
