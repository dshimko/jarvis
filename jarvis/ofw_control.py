"""OFW control calls made in code, never by the model (AD38, AD39). Personal daemon only.

status(mode) uses the read token (`ofw_status` reads the server's local state and never contacts OFW).
confirm_privileged(mode, id) and reset_breaker(mode) use the executor-only write token. All three go through
the outbox executor's MCP call path. Results are reduced to a status word plus, for status(), a whitelist
of metadata fields; OFW content never comes back. Errors return "error: <ErrorClass>" and log
ofw_control_error with error_class only.
"""
from __future__ import annotations
import asyncio, logging, re
from . import ofw_result, outbox
from .logsetup import log_event
from .modes import Mode

log = logging.getLogger(__name__)
SERVER = outbox.OFW_SERVER
OFW_MODE = "personal"
STATUS_TOOL, CONFIRM_TOOL, RESET_TOOL = "ofw_status", "confirm_privileged", "reset_breaker"
ID_RE = re.compile(r"[0-9]{1,20}")            # ASCII digits only; always used with fullmatch
BAD_ID_REFUSAL = "Refused: the id after `privileged ok` must be 1 to 20 digits. Nothing was sent."
ERROR_PREFIX = "error: "
# ofw_status fields kept, each with the pattern its whole value must fullmatch (anything else is dropped).
_WORD = re.compile(r"[a-z_]{1,32}")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?"
                        r"(Z|[+-][0-9]{2}:[0-9]{2})?")
_ERROR_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
STATUS_FIELDS = {"breaker": _WORD, "breaker_opened_at": _TIMESTAMP, "breaker_reason": _ERROR_CLASS,
                 "last_login_at": _TIMESTAMP}
STATUS_FLAGS = ("session_valid", "writes_enabled")
STATUS_COUNTS = ("calls_this_hour",)


def _error(e: Exception) -> str:
    log_event(log, "ofw_control_error", logging.ERROR, error_class=type(e).__name__)
    return ERROR_PREFIX + type(e).__name__


def _call(mode: Mode, tool: str, args: dict, *, write: bool):
    conf = outbox._server_conf(mode, SERVER, write=write)
    return asyncio.run(outbox._call(conf, tool, args))


def _word(result) -> str:
    return ofw_result.status_of(result) or ofw_result.NO_STATUS


def _fields(result) -> dict:
    data = ofw_result.payload_of(result)
    kept = {k: data[k] for k, rx in STATUS_FIELDS.items() if isinstance(data.get(k), str) and rx.fullmatch(data[k])}
    kept.update({k: data[k] for k in STATUS_FLAGS if isinstance(data.get(k), bool)})
    kept.update({k: data[k] for k in STATUS_COUNTS if type(data.get(k)) is int})
    return kept


def status(mode: Mode) -> dict:
    """{"status": <word>, plus whitelisted ofw_status fields}; {"status": "error: <Class>"} on failure."""
    try:
        result = _call(mode, STATUS_TOOL, {}, write=False)
    except Exception as e:
        return {"status": _error(e)}
    return {"status": _word(result), **_fields(result)}


def confirm_privileged(mode: Mode, item_id: str) -> str:
    """Release exactly one privileged OFW id. The caller has already checked the id against ID_RE."""
    if not ID_RE.fullmatch(str(item_id)):
        return ERROR_PREFIX + "BadId"
    try:
        return _word(_call(mode, CONFIRM_TOOL, {"id": str(item_id)}, write=True))
    except Exception as e:
        return _error(e)


def reset_breaker(mode: Mode) -> str:
    try:
        return _word(_call(mode, RESET_TOOL, {}, write=True))
    except Exception as e:
        return _error(e)


def has_write_token(mode: Mode) -> bool:
    return bool(mode.env.get(outbox.WRITE_TOKEN_KEYS[SERVER]))
