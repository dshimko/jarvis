"""httpx client for the local Jarvis daemon API (see the Phase 2 API contract).

Cross-platform: httpx has no Windows-only dependency, so this module is fully unit-testable.
Never touches \\\\wsl$ and never reads env files (C8) -- it only ever calls the loopback HTTP API
and reads the plain-text token file the installer writes to %LOCALAPPDATA%\\Jarvis\\api_token.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

import httpx

log = logging.getLogger(__name__)

UTTERANCE_TIMEOUT = 960.0  # the brain can take up to 900s
DEFAULT_TIMEOUT = 10.0
SSE_BACKOFF_START = 1.0
SSE_BACKOFF_MAX = 30.0


SAFE_DETAIL_STATUS_CODES = (400, 404, 409)


class ApiError(Exception):
    """Raised for any non-2xx response. The exception's message (str/repr/args) is always a
    fixed, safe string -- NEVER the response body, which can echo request input (e.g. a 422
    validation error echoing the submitted utterance text back verbatim). `status_code` lets
    callers pick a friendly message. `server_detail` optionally carries the server's own
    `detail` string, but ONLY for 400/404/409 (contract errors whose message text is known to be
    safe) -- it is for a caller to SPEAK to the user, and must never be logged."""

    def __init__(self, status_code: int, server_detail: str | None = None):
        super().__init__(f"API request failed with status {status_code}")
        self.status_code = status_code
        self.server_detail = server_detail


def read_token(token_path: str) -> str:
    """Reads the bearer token fresh every time, so a regenerated token is picked up without a
    client restart."""
    return Path(token_path).read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: dict


def parse_sse_stream(lines: Iterable[str]) -> Iterator[SSEEvent]:
    """Parses `event: <type>` / `data: <json>` frames from an iterable of already-split lines (no
    trailing newline), as returned by httpx.Response.iter_lines(). A blank line ends a frame.
    Lines starting with ':' are comments (the 15s heartbeat) and are skipped. Tolerates unknown
    event types and malformed JSON (yielded as an empty dict) so one bad frame doesn't kill the
    stream."""
    event_name = "message"
    data_lines: list[str] = []
    for line in lines:
        if line == "":
            if data_lines:
                yield SSEEvent(event_name, _parse_data("\n".join(data_lines)))
            event_name, data_lines = "message", []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())


def _parse_data(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("SSE frame with malformed JSON payload, ignoring")
        return {}
    return payload if isinstance(payload, dict) else {}


class JarvisApi:
    """One instance per client process. Holds an httpx.Client for connection reuse; pass
    `transport` (e.g. httpx.MockTransport) to test without a network."""

    def __init__(self, base_url: str, token_path: str, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), transport=transport)
        self.token_path = token_path

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {read_token(self.token_path)}"}

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """Never reads the response body except for the narrow, contract-defined 400/404/409
        `detail` (H2): a 422 (validation) or 5xx body can echo request input and must never be
        turned into an exception message or attribute."""
        if resp.status_code < 400:
            return
        detail = None
        if resp.status_code in SAFE_DETAIL_STATUS_CODES:
            try:
                detail = resp.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
        raise ApiError(resp.status_code, server_detail=detail)

    def health(self) -> dict:
        resp = self._client.get("/health", headers=self._headers(), timeout=DEFAULT_TIMEOUT)
        self._raise_for_status(resp)
        return resp.json()

    def utterance(self, text: str, mode: str, mode_confirmed: bool = False) -> dict:
        body = {"text": text, "mode": mode, "mode_confirmed": mode_confirmed}
        resp = self._client.post("/utterance", json=body, headers=self._headers(),
                                  timeout=UTTERANCE_TIMEOUT)
        self._raise_for_status(resp)
        return resp.json()

    def outbox(self, mode: str) -> list[dict]:
        resp = self._client.get("/outbox", params={"mode": mode}, headers=self._headers(),
                                 timeout=DEFAULT_TIMEOUT)
        self._raise_for_status(resp)
        return resp.json().get("items", [])

    def approve(self, mode: str, item_id: str, body_sha256: str, readback_sha256: str,
                reconfirm: bool = False) -> dict:
        body = {"mode": mode, "id": item_id, "body_sha256": body_sha256,
                 "readback_sha256": readback_sha256, "reconfirm": reconfirm}
        resp = self._client.post("/approve", json=body, headers=self._headers(),
                                  timeout=DEFAULT_TIMEOUT)
        self._raise_for_status(resp)
        return resp.json()

    def iter_events(self, sleep: Callable[[float], None] | None = None) -> Iterator[SSEEvent]:
        """Streams /events forever, reconnecting with exponential backoff on any failure. Rereads
        the token on every (re)connect."""
        sleep = sleep or time.sleep
        delay = SSE_BACKOFF_START
        while True:
            try:
                timeout = httpx.Timeout(DEFAULT_TIMEOUT, read=None)
                with self._client.stream("GET", "/events", headers=self._headers(),
                                          timeout=timeout) as resp:
                    self._raise_for_status(resp)
                    delay = SSE_BACKOFF_START
                    yield from parse_sse_stream(resp.iter_lines())
            except (httpx.HTTPError, ApiError, OSError) as exc:
                log.warning("SSE disconnected (%s), reconnecting in %.0fs", type(exc).__name__, delay)
                sleep(delay)
                delay = min(delay * 2, SSE_BACKOFF_MAX)
