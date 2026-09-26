"""JarvisApi against httpx.MockTransport, driven by a TokenSource: it asks the source for the
current token on every call, refreshes it (and retries once) on a 401, and non-2xx responses
raise ApiError with the status code callers key friendly messages off of.

FileToken re-reads a plain token file on every get() (existing local/wsl behavior, unchanged).
SecretsManagerToken fetches via a subprocess call to the AWS CLI, caches in memory, and -- the
AD16 requirement this file exists to prove -- never writes the token to disk under the aws
profile.
"""
import json
import subprocess

import httpx
import pytest

from jarvis_client.api import (
    ApiError,
    DualModeApi,
    FileToken,
    SecretsManagerToken,
    JarvisApi,
    SSEEvent,
    TokenFetchError,
    read_token,
)


class FakeTokenSource:
    """A TokenSource test double: `get()` returns `token`, `refresh()` swaps in `next_token` (if
    given) and records how many times each was called."""

    def __init__(self, token="tok-1", next_token=None):
        self.token = token
        self.next_token = next_token if next_token is not None else token
        self.get_calls = 0
        self.refresh_calls = 0

    def get(self) -> str:
        self.get_calls += 1
        return self.token

    def refresh(self) -> str:
        self.refresh_calls += 1
        self.token = self.next_token
        return self.token


def _client(handler, token="tok-1", next_token=None):
    source = FakeTokenSource(token=token, next_token=next_token)
    return JarvisApi("http://localhost:8781", source, transport=httpx.MockTransport(handler)), source


def test_read_token_strips_whitespace(tmp_path):
    path = tmp_path / "api_token"
    path.write_text("  abc123  \n")

    assert read_token(str(path)) == "abc123"


def test_health_sends_bearer_token_and_returns_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True, "mode": "work", "deployment": "aws"})

    api, _ = _client(handler)

    result = api.health()

    assert result == {"ok": True, "mode": "work", "deployment": "aws"}
    assert seen["auth"] == "Bearer tok-1"


def test_token_source_is_asked_fresh_on_every_call():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"ok": True, "modes": {}})

    api, source = _client(handler)
    api.health()
    source.token = "tok-2"
    api.health()

    assert seen == ["Bearer tok-1", "Bearer tok-2"]
    assert source.get_calls == 2
    assert source.refresh_calls == 0


def test_401_refreshes_the_token_once_and_retries():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        seen.append(auth)
        if auth == "Bearer tok-1":
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True, "mode": "work", "deployment": "aws"})

    api, source = _client(handler, token="tok-1", next_token="tok-2")

    result = api.health()

    assert result["ok"] is True
    assert seen == ["Bearer tok-1", "Bearer tok-2"]
    assert source.refresh_calls == 1


def test_401_that_persists_after_refresh_raises_api_error_not_a_retry_loop():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401)

    api, source = _client(handler, token="tok-1", next_token="tok-2")

    with pytest.raises(ApiError) as exc_info:
        api.health()

    assert exc_info.value.status_code == 401
    assert len(calls) == 2  # exactly one retry, never a loop
    assert source.refresh_calls == 1


def test_non_2xx_raises_api_error_with_status_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "hash mismatch"})

    api, _ = _client(handler)

    with pytest.raises(ApiError) as exc_info:
        api.approve("work", "id-1", "a" * 64, "b" * 64)

    assert exc_info.value.status_code == 409
    assert exc_info.value.server_detail == "hash mismatch"  # 409 is a safe-to-carry status
    assert "hash mismatch" not in str(exc_info.value)  # H2: message itself is always fixed


def test_utterance_posts_expected_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "ok", "mode_used": "work", "needs_mode": False,
                                          "suggested_mode": None})

    api, _ = _client(handler)

    result = api.utterance("hello", "work", mode_confirmed=True)

    assert captured["body"] == {"text": "hello", "mode": "work", "mode_confirmed": True}
    assert result["reply"] == "ok"


def test_outbox_returns_items_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "personal"
        return httpx.Response(200, json={"items": [{"id": "a"}]})

    api, _ = _client(handler)

    assert api.outbox("personal") == [{"id": "a"}]


def test_422_body_never_reaches_the_exception_message_or_server_detail():
    """H2: a 422 (pydantic validation error) can echo the submitted text back in `detail`. That
    text must never end up in ApiError's message/args, and must never be captured at all (422 is
    not in the safe-to-carry status set)."""
    marker = "XYZZY-SECRET-UTTERANCE-3f9a"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": f"string too long: {marker}"})

    api, _ = _client(handler)

    with pytest.raises(ApiError) as exc_info:
        api.utterance(marker, "work")

    exc = exc_info.value
    assert exc.status_code == 422
    assert exc.server_detail is None
    assert marker not in str(exc)
    assert marker not in repr(exc)
    assert all(marker not in str(arg) for arg in exc.args)


def test_500_body_is_never_captured_either():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error: sensitive stack trace")

    api, _ = _client(handler)

    with pytest.raises(ApiError) as exc_info:
        api.health()

    assert exc_info.value.server_detail is None
    assert "stack trace" not in str(exc_info.value)


# -- FileToken -------------------------------------------------------------------------------

def test_file_token_rereads_the_file_on_every_get(tmp_path):
    path = tmp_path / "api_token"
    path.write_text("tok-1")
    source = FileToken(str(path))

    assert source.get() == "tok-1"
    path.write_text("tok-2")
    assert source.get() == "tok-2"
    assert source.refresh() == "tok-2"


# -- SecretsManagerToken ----------------------------------------------------------------------

class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_secrets_manager_token_calls_the_aws_cli_with_expected_args(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return _FakeCompletedProcess(stdout="tok-from-secretsmanager\n")

    source = SecretsManagerToken("jarvis-client-sso", "jarvis/work/api-token", runner=runner)

    assert source.get() == "tok-from-secretsmanager"
    assert calls == [[
        "aws", "secretsmanager", "get-secret-value",
        "--profile", "jarvis-client-sso", "--secret-id", "jarvis/work/api-token",
        "--query", "SecretString", "--output", "text", "--region", "us-east-2",
    ]]


def test_secrets_manager_token_caches_until_refresh(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return _FakeCompletedProcess(stdout=f"tok-{len(calls)}\n")

    source = SecretsManagerToken("p", "jarvis/personal/api-token", runner=runner)

    assert source.get() == "tok-1"
    assert source.get() == "tok-1"  # cached, no second subprocess call
    assert len(calls) == 1
    assert source.refresh() == "tok-2"
    assert source.get() == "tok-2"
    assert len(calls) == 2


def test_secrets_manager_token_failure_raises_without_leaking_cli_output(tmp_path):
    def runner(cmd, **kw):
        return _FakeCompletedProcess(returncode=1, stderr="AccessDeniedException: sensitive detail")

    source = SecretsManagerToken("p", "jarvis/work/api-token", runner=runner)

    with pytest.raises(TokenFetchError) as exc_info:
        source.get()
    assert "sensitive detail" not in str(exc_info.value)


def test_secrets_manager_token_never_writes_to_disk(tmp_path, monkeypatch):
    """AD16 / PLAN rule: tokens are never written to disk on the workstation under the aws
    profile. Asserts on the observable behavior: nothing appears under tmp_path after several
    get()/refresh() calls, using a cwd sandbox so any errant relative-path write would land here."""
    monkeypatch.chdir(tmp_path)

    def runner(cmd, **kw):
        return _FakeCompletedProcess(stdout="tok-xyz\n")

    source = SecretsManagerToken("p", "jarvis/work/api-token", runner=runner)
    source.get()
    source.refresh()

    assert list(tmp_path.iterdir()) == []


# -- iter_events (H6) -------------------------------------------------------------------------

def test_iter_events_refreshes_the_token_once_on_a_401_then_streams():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.headers.get("authorization"))
        if attempts[-1] == "Bearer tok-1":
            return httpx.Response(401)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                               text='event: schedule_done\ndata: {"ok": true}\n\n')

    source = FakeTokenSource(token="tok-1", next_token="tok-2")
    api = JarvisApi("http://localhost:8781", source, transport=httpx.MockTransport(handler))

    gen = api.iter_events(sleep=lambda s: None)
    try:
        first_event = next(gen)
        assert first_event == SSEEvent("schedule_done", {"ok": True})
        assert attempts == ["Bearer tok-1", "Bearer tok-2"]
    finally:
        gen.close()


def test_iter_events_catches_token_fetch_error_and_backs_off_then_recovers():
    """H6: a TokenFetchError from the token source (e.g. the AWS CLI hiccups) must not crash the
    generator (and thus the notify/toast thread that owns it) -- it backs off like any other
    disconnect and retries."""
    calls = {"n": 0}

    class FlakyTokenSource:
        def get(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TokenFetchError("cli hiccup")
            return "tok-ok"

        def refresh(self):
            return self.get()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                               text='event: schedule_done\ndata: {"ok": true}\n\n')

    sleeps = []
    api = JarvisApi("http://localhost:8781", FlakyTokenSource(), transport=httpx.MockTransport(handler))

    gen = api.iter_events(sleep=sleeps.append)
    try:
        first_event = next(gen)
        assert sleeps == [1.0]  # exactly one backoff before it recovered
        assert first_event == SSEEvent("schedule_done", {"ok": True})
    finally:
        gen.close()


def test_iter_events_catches_subprocess_error_from_the_token_source_too():
    calls = {"n": 0}

    class FlakySubprocessTokenSource:
        def get(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd=["aws"], timeout=15)
            return "tok-ok"

        def refresh(self):
            return self.get()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                               text='event: schedule_done\ndata: {"ok": true}\n\n')

    sleeps = []
    api = JarvisApi("http://localhost:8781", FlakySubprocessTokenSource(),
                     transport=httpx.MockTransport(handler))

    gen = api.iter_events(sleep=sleeps.append)
    try:
        first_event = next(gen)
        assert sleeps == [1.0]
        assert first_event == SSEEvent("schedule_done", {"ok": True})
    finally:
        gen.close()


# -- DualModeApi (M9) --------------------------------------------------------------------------

def test_dual_mode_api_routes_each_call_to_the_matching_per_mode_client():
    class FakeClient:
        def __init__(self, name):
            self.name = name
            self.calls = []

        def utterance(self, text, mode, mode_confirmed=False):
            self.calls.append(("utterance", text, mode, mode_confirmed))
            return {"reply": f"handled by {self.name}"}

        def outbox(self, mode):
            self.calls.append(("outbox", mode))
            return [{"id": self.name}]

        def approve(self, mode, item_id, body_sha256, readback_sha256, reconfirm=False):
            self.calls.append(("approve", mode, item_id, body_sha256, readback_sha256, reconfirm))
            return {"result": f"approved by {self.name}"}

    work = FakeClient("work")
    personal = FakeClient("personal")
    dual = DualModeApi({"work": work, "personal": personal})

    assert dual.utterance("hi", "work")["reply"] == "handled by work"
    assert dual.utterance("hi", "personal")["reply"] == "handled by personal"
    assert dual.outbox("personal") == [{"id": "personal"}]
    assert dual.approve("work", "id-1", "a" * 64, "b" * 64)["result"] == "approved by work"

    assert work.calls == [("utterance", "hi", "work", False), ("approve", "work", "id-1", "a" * 64, "b" * 64, False)]
    assert personal.calls == [("utterance", "hi", "personal", False), ("outbox", "personal")]
