"""One Telegram notice per OFW breaker opening (AD39). Personal daemon only, called by the Gmail watcher.

After each `/ofw-notify` run the watcher asks ofw-mcp for its local status (`ofw_status`, read token). When
the breaker is open with a `breaker_opened_at` not yet reported, one fixed-text notice is pushed and the
timestamp is recorded in ~/.jarvis/ofw_breaker_notified.json (0600), so a restart does not repeat it. The
notice carries only the breaker's error class. Failures log error_class only and never raise.
"""
from __future__ import annotations
import json, logging, re
from pathlib import Path
from typing import Callable
from . import ledger, paths
from .logsetup import log_event

log = logging.getLogger("jarvis.gmail_watch")
NOTIFIED_FILE = "ofw_breaker_notified.json"
NOTICE = ("OFW login is locked, breaker open: {error_class}. Fix the credentials or challenge, "
          "then send `ofw reset`.")
BREAKER_OPEN = "open"
UNKNOWN_CLASS = "unknown"
ERROR_PREFIX = "error: "
ERROR_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")     # always used with fullmatch


def _safe_class(value) -> str:
    return value if isinstance(value, str) and ERROR_CLASS_RE.fullmatch(value) else UNKNOWN_CLASS


class BreakerNotifier:
    def __init__(self, status: Callable[[object], dict], notify: Callable[[str], bool] | None):
        self._status, self._notify = status, notify
        self.pending = False        # an opening was seen but its push failed; the watcher retries every poll

    def _path(self) -> Path:
        d = paths.jarvis_dir()
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        return d / NOTIFIED_FILE

    def _recorded(self) -> str | None:
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log_event(log, "ofw_breaker_notified_reset", logging.WARNING)
            return None
        return data.get("breaker_opened_at") if isinstance(data, dict) else None

    def _record(self, opened_at: str) -> None:
        ledger.atomic_write_text(self._path(), json.dumps({"breaker_opened_at": opened_at}))

    def _read_status(self, mode) -> dict | None:
        try:
            st = self._status(mode)
        except Exception as e:
            log_event(log, "ofw_status_error", logging.ERROR, error_class=type(e).__name__)
            return None
        if not isinstance(st, dict):
            log_event(log, "ofw_status_error", logging.ERROR, error_class="BadStatus")
            return None
        word = st.get("status")
        if isinstance(word, str) and word.startswith(ERROR_PREFIX):
            log_event(log, "ofw_status_error", logging.ERROR, error_class=_safe_class(word[len(ERROR_PREFIX):]))
            return None
        return st

    def _send(self, text: str) -> bool:
        try:
            return bool(self._notify(text))
        except Exception as e:
            log_event(log, "ofw_watch_error", logging.ERROR, error_class=type(e).__name__)
            return False

    def check(self, mode) -> None:
        """Push the notice once per breaker_opened_at. Never raises."""
        try:
            self._check(mode)
        except Exception as e:                           # e.g. an unwritable ~/.jarvis
            log_event(log, "ofw_watch_error", logging.ERROR, error_class=type(e).__name__)

    def _check(self, mode) -> None:
        st = self._read_status(mode)
        if st is None:
            return                                          # status error: a pending retry stays pending
        opened_at = st.get("breaker_opened_at")
        if st.get("breaker") != BREAKER_OPEN or not isinstance(opened_at, str) or not opened_at \
                or opened_at == self._recorded():
            self.pending = False
            return
        error_class = _safe_class(st.get("breaker_reason"))
        if self._notify is None:
            log_event(log, "ofw_breaker_open", logging.WARNING, error_class=error_class)
        elif not self._send(NOTICE.format(error_class=error_class)):
            self.pending = True                             # retried on the next poll, new ids or not
            return
        self._record(opened_at)
        self.pending = False
