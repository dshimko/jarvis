"""Text-to-speech: Piper (preferred, if configured and present) or Windows SAPI via pyttsx3.

winsound/pyttsx3 are imported lazily inside functions so this module can be imported on any
platform (winsound only exists on Windows).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

PIPER_TIMEOUT_SECONDS = 60


def speak(text: str, piper_exe: str | None, piper_voice: str | None) -> None:
    """Speaks `text` verbatim. Piper is used only when both paths are configured and exist on
    disk; any Piper failure falls back to SAPI rather than staying silent."""
    if piper_exe and piper_voice and os.path.exists(piper_exe) and os.path.exists(piper_voice):
        try:
            _speak_piper(text, piper_exe, piper_voice)
            return
        except (OSError, subprocess.SubprocessError, UnicodeError):
            log.exception("piper failed, falling back to SAPI")
    _speak_sapi(text)


def _speak_piper(text: str, piper_exe: str, piper_voice: str) -> None:
    import winsound

    fd, raw_path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-tts-")
    os.close(fd)
    wav_path = Path(raw_path)
    try:
        subprocess.run([piper_exe, "--model", piper_voice, "--output_file", str(wav_path)],
                       input=text, encoding="utf-8", capture_output=True,
                       timeout=PIPER_TIMEOUT_SECONDS, check=True)
        winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)
    finally:
        wav_path.unlink(missing_ok=True)


def _speak_sapi(text: str) -> None:
    import pyttsx3

    engine = pyttsx3.init()
    try:
        engine.say(text)
        engine.runAndWait()
    finally:
        engine.stop()
