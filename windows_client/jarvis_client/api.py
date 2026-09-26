"""httpx client for the local Jarvis daemon API (see the Phase 2 API contract).

Cross-platform: httpx has no Windows-only dependency, so this module is fully unit-testable.
Never touches \\\\wsl$ and never reads env files (C8) -- it only ever calls the loopback HTTP API
and reads the plain-text token file the installer writes to %LOCALAPPDATA%\\Jarvis\\api_token.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Protocol

import httpx

log = logging.getLogger(__name__)

UTTERANCE_TIMEOUT = 960.0  # the brain can take up to 900s
DEFAULT_TIMEOUT = 10.0
SECRETSMANAGER_TIMEOUT = 15.0
SSE_BACKOFF_START = 1.0
SSE_BACKOFF_MAX = 30.0
AWS_REGION = "us-east-1"  # region-deny SCP consequence (PLAN.md 3.2, AD42): every aws call is pinned

# LOW: on Windows, a plain subprocess.run of aws.exe briefly flashes a console window on every
# token fetch/refresh; CREATE_NO_WINDOW suppresses it. The flag only exists in the `subprocess`
# module on Windows builds, so it's looked up lazily and only applied there.
_SUBPROCESS_NO_WINDOW_KW: dict = {}
if sys.platform == "win32":
    _SUBPROCESS_NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW}


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


class TokenFetchError(Exception):
    """Raised by a TokenSource when it cannot produce a token at all (as opposed to ApiError,
    which is the server rejecting a token it received)."""


class TokenSource(Protocol):
    """`get()` returns the current best-known token (cached where that makes sense); `refresh()`
    forces a fresh fetch and returns the new value. JarvisApi calls `refresh()` at most once per
    request, on a 401 -- never in a retry loop."""

    def get(self) -> str: ...
    def refresh(self) -> str: ...


class FileToken:
    """Reads the bearer token fresh from `token_path` on every get() -- unchanged local/wsl
    behavior (a regenerated token is picked up without a client restart). refresh() is the same
    read: a file has no server-side "stale" concept beyond what's already been written to it."""

    def __init__(self, token_path: str):
        self.token_path = token_path

    def get(self) -> str:
        return read_token(self.token_path)

    def refresh(self) -> str:
        return self.get()


class SecretsManagerToken:
    """AD16: `aws secretsmanager get-secret-value --profile <p> --secret-id <id> --query
    SecretString --output text --region <region>` via subprocess, held in memory only -- never
    written to disk under the aws profile, never logged. `region` comes from
    ClientConfig.aws_region (AD42: region literals leave every script, including this one) and
    defaults to AWS_REGION when the caller doesn't pass one. Cached after the first fetch;
    refresh() always re-fetches (called by JarvisApi once per request, on a 401)."""

    def __init__(self, aws_profile: str, secret_id: str, region: str = AWS_REGION,
                 runner: Callable[..., "subprocess.CompletedProcess[str]"] | None = None):
        self._aws_profile = aws_profile
        self._secret_id = secret_id
        self._region = region
        self._runner = runner or subprocess.run
        self._cached: str | None = None

    def get(self) -> str:
        if self._cached is None:
            self._cached = self._fetch()
        return self._cached

    def refresh(self) -> str:
        self._cached = self._fetch()
        return self._cached

    def _fetch(self) -> str:
        result = self._runner(
            ["aws", "secretsmanager", "get-secret-value", "--profile", self._aws_profile,
             "--secret-id", self._secret_id, "--query", "SecretString", "--output", "text",
             "--region", self._region],
            capture_output=True, text=True, timeout=SECRETSMANAGER_TIMEOUT,
            **_SUBPROCESS_NO_WINDOW_KW,
        )
        if result.returncode != 0:
            # Never include stdout/stderr: a failure message from the CLI could, in principle,
            # echo request context; the exit code is enough to act on.
            raise TokenFetchError(f"secretsmanager get-secret-value failed (exit {result.returncode})")
        token = result.stdout.strip()
        if not token:
            raise TokenFetchError("secretsmanager get-secret-value returned an empty value")
        return token


class JarvisApi:
    """One instance per (client process, mode) pair -- AD4: each daemon serves exactly one mode,
    so a work JarvisApi and a personal JarvisApi point at two different base_urls. Holds an
    httpx.Client for connection reuse; pass `transport` (e.g. httpx.MockTransport) to test without
    a network."""

    def __init__(self, base_url: str, token_source: TokenSource,
                 transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), transport=transport)
        self._token_source = token_source

    def close(self) -> None:
        self._client.close()

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

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

    def _request(self, method: str, path: str, *, timeout: float, **kw) -> httpx.Response:
        """Sends with the current token; on a 401, refreshes the token source once and retries
        exactly once more (a stolen/rotated/stale token is refetched, not retried in a loop)."""
        resp = self._client.request(method, path, headers=self._headers(self._token_source.get()),
                                     timeout=timeout, **kw)
        if resp.status_code == 401:
            token = self._token_source.refresh()
            resp = self._client.request(method, path, headers=self._headers(token),
                                         timeout=timeout, **kw)
        self._raise_for_status(resp)
        return resp

    def health(self) -> dict:
        return self._request("GET", "/health", timeout=DEFAULT_TIMEOUT).json()

    def utterance(self, text: str, mode: str, mode_confirmed: bool = False) -> dict:
        body = {"text": text, "mode": mode, "mode_confirmed": mode_confirmed}
        return self._request("POST", "/utterance", json=body, timeout=UTTERANCE_TIMEOUT).json()

    def outbox(self, mode: str) -> list[dict]:
        resp = self._request("GET", "/outbox", params={"mode": mode}, timeout=DEFAULT_TIMEOUT)
        return resp.json().get("items", [])

    def approve(self, mode: str, item_id: str, body_sha256: str, readback_sha256: str,
                reconfirm: bool = False) -> dict:
        body = {"mode": mode, "id": item_id, "body_sha256": body_sha256,
                 "readback_sha256": readback_sha256, "reconfirm": reconfirm}
        return self._request("POST", "/approve", json=body, timeout=DEFAULT_TIMEOUT).json()

    def iter_events(self, sleep: Callable[[float], None] | None = None) -> Iterator[SSEEvent]:
        """Streams /events forever, reconnecting with exponential backoff on any failure. Rereads
        the token (refreshing once on a 401) on every (re)connect."""
        sleep = sleep or time.sleep
        delay = SSE_BACKOFF_START
        while True:
            try:
                timeout = httpx.Timeout(DEFAULT_TIMEOUT, read=None)
                headers = self._headers(self._token_source.get())
                with self._client.stream("GET", "/events", headers=headers, timeout=timeout) as resp:
                    if resp.status_code == 401:
                        headers = self._headers(self._token_source.refresh())
                        with self._client.stream("GET", "/events", headers=headers,
                                                  timeout=timeout) as retried:
                            self._raise_for_status(retried)
                            delay = SSE_BACKOFF_START
                            yield from parse_sse_stream(retried.iter_lines())
                        continue
                    self._raise_for_status(resp)
                    delay = SSE_BACKOFF_START
                    yield from parse_sse_stream(resp.iter_lines())
            except (httpx.HTTPError, ApiError, OSError, TokenFetchError,
                    subprocess.SubprocessError) as exc:
                # H6: a SecretsManagerToken can fail transiently (CLI hiccup, SSO token needing a
                # refresh) -- that must back off and retry like any other disconnect, never crash
                # the notify/toast thread that owns this generator.
                log.warning("SSE disconnected (%s), reconnecting in %.0fs", type(exc).__name__, delay)
                sleep(delay)
                delay = min(delay * 2, SSE_BACKOFF_MAX)


class DualModeApi:
    """Implements the same call surface `jarvis_client.flow.Api` expects (utterance/outbox/
    approve, each taking `mode`), by dispatching to the matching per-mode JarvisApi. AD4 means
    there is no single endpoint that understands both modes any more, but flow.py's voice state
    machine should not need to know that -- it just calls `.utterance(text, mode, ...)` and this
    routes it. Built once in __main__.py from the two JarvisApi instances."""

    def __init__(self, apis: dict[str, JarvisApi]):
        self._apis = apis

    def utterance(self, text: str, mode: str, mode_confirmed: bool = False) -> dict:
        return self._apis[mode].utterance(text, mode, mode_confirmed)

    def outbox(self, mode: str) -> list[dict]:
        return self._apis[mode].outbox(mode)

    def approve(self, mode: str, item_id: str, body_sha256: str, readback_sha256: str,
                reconfirm: bool = False) -> dict:
        return self._apis[mode].approve(mode, item_id, body_sha256, readback_sha256, reconfirm)
