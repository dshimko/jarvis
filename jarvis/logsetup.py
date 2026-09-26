"""Structured logging with a content filter (AD11, tightened in PLAN 3.2 and DESIGN 7.1).

One JSON object per line on stdout: ts, level, mode, logger, event, msg, plus metadata fields (id,
duration_ms, status, error_class, job, channel, ...). Three controls keep content off the box:
  1. ContentFilter, on the handler, drops any field named in DROP_KEYS, recursively and case-insensitively.
  2. Third-party loggers that echo URLs or payloads (httpx logs the Telegram bot token) are pinned to WARNING.
  3. msg passes through the daemon's redactor; under the aws profile an exception is reduced to error_class
     (no exc_text, exc_info, stack_info, or traceback).
JARVIS_LOG_FORMAT=text gives readable console output for local dev; the aws profile is always JSON.
"""
from __future__ import annotations
import datetime as dt, json, logging, os, sys
from typing import Callable, TextIO

DROP_KEYS = frozenset({"body", "text", "draft", "preview", "snippet", "subject", "transcript",
                       "readback", "reply", "prompt", "first_line", "args", "utterance", "message_text"})
QUIET_LOGGERS = ("httpx", "httpcore", "telegram", "slack_bolt", "slack_sdk", "mcp", "urllib3", "googleapiclient")
EVENT_ATTR, FIELDS_ATTR = "jarvis_event", "jarvis_fields"
HANDLER_MARK = "_jarvis_handler"
FORMAT_ENV = "JARVIS_LOG_FORMAT"
CORE_KEYS = ("ts", "level", "mode", "logger", "event", "msg")
EXC_KEYS = ("exc_info", "exc_text", "stack_info", "traceback")
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime",
                                                                                   "taskName"}
_REDACTOR: list[Callable[[str], str]] = []


def set_redactor(fn: Callable[[str], str] | None) -> None:
    """main wires the daemon's redactor once its mode is loaded; identity until then."""
    _REDACTOR.clear()
    if fn:
        _REDACTOR.append(fn)


def _redact(text: str) -> str:
    return _REDACTOR[0](text) if _REDACTOR else text


def drop_content(obj):
    """A copy of obj without DROP_KEYS at any depth (dict keys compared case-insensitively)."""
    if isinstance(obj, dict):
        return {k: drop_content(v) for k, v in obj.items() if not (isinstance(k, str) and k.lower() in DROP_KEYS)}
    if isinstance(obj, (list, tuple)):
        return [drop_content(v) for v in obj]
    return obj


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    """A metadata-only record: `event` is the message; fields travel in one extra attribute, filtered."""
    logger.log(level, event, extra={EVENT_ATTR: event, FIELDS_ATTR: fields})


def _extras(record: logging.LogRecord) -> dict:
    plain = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and k not in (EVENT_ATTR, FIELDS_ATTR)}
    return {**plain, **(getattr(record, FIELDS_ATTR, None) or {})}


class ContentFilter(logging.Filter):
    """Replaces the record's extra fields with filtered copies. Never blocks a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for k in [k for k in vars(record) if k not in _STANDARD_ATTRS]:
            if k.lower() in DROP_KEYS:
                delattr(record, k)
            elif k == FIELDS_ATTR or isinstance(getattr(record, k), (dict, list, tuple)):
                setattr(record, k, drop_content(getattr(record, k)))
        return True


def _error_class(record: logging.LogRecord) -> str | None:
    exc = record.exc_info
    return exc[0].__name__ if exc and exc[0] else None


class JsonFormatter(logging.Formatter):
    def __init__(self, mode: str, deployment: str):
        super().__init__()
        self.mode, self.strict = mode, deployment == "aws"

    def format(self, record: logging.LogRecord) -> str:
        fields = drop_content(_extras(record))
        err = _error_class(record)
        out = {**{k: v for k, v in fields.items() if k not in CORE_KEYS + EXC_KEYS},
               "ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname, "mode": self.mode, "logger": record.name,
               "event": getattr(record, EVENT_ATTR, None), "msg": _redact(record.getMessage())}
        if err and not out.get("error_class"):
            out["error_class"] = err
        if not self.strict and record.exc_info:
            out["exc_text"] = _redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Local dev only: `LEVEL logger: msg key=value ...`, same filtering and redaction as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        fields = drop_content(_extras(record))
        tail = "".join(f" {k}={v}" for k, v in fields.items() if v is not None)
        line = f"{record.levelname} {record.name}: {_redact(record.getMessage())}{tail}"
        if record.exc_info:
            line += "\n" + _redact(self.formatException(record.exc_info))
        return line


def configure(mode: str, fmt: str | None = None, deployment: str = "local",
              stream: TextIO | None = None, level: int = logging.INFO) -> logging.Handler:
    """Install the one Jarvis handler on the root logger (replacing a previous one) and pin QUIET_LOGGERS."""
    fmt = "json" if deployment == "aws" else (fmt or os.environ.get(FORMAT_ENV) or "json")
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(mode, deployment) if fmt == "json" else TextFormatter())
    handler.addFilter(ContentFilter())
    setattr(handler, HANDLER_MARK, True)
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, HANDLER_MARK, False)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
