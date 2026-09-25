"""Jarvis Windows tray client: hotkeys, local STT/TTS, and the pending-item tray UI.

Talks to the WSL2 daemon over the local HTTP API (see PORTING.md section E). Hardware and
Windows-only modules (`stt`, `tts`, `tray`, `notify`) import their third-party deps lazily inside
functions so `flow.py` and `api.py` can be unit-tested on any platform.
"""
