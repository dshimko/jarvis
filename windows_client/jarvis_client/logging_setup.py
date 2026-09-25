"""Rotating file log at %LOCALAPPDATA%\\Jarvis\\client.log.

Logs NEVER contain utterance text, transcript, reply text, or readback in any mode -- only event
kinds, mode, lengths, and status codes (PORTING.md, "Client logging"). Callers must keep to that
contract; this module only wires up the handler.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 1_000_000
BACKUP_COUNT = 3


def setup_logging(log_path: str, level: int = logging.INFO) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
