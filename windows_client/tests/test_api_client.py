"""JarvisApi against httpx.MockTransport: it re-reads the token file on every call, and non-2xx
responses raise ApiError with the status code callers key friendly messages off of."""
import json

import httpx
import pytest

from jarvis_client.api import ApiError, JarvisApi, read_token


def _client(handler, tmp_path, token="tok-1"):
    token_path = tmp_path / "api_token"
    token_path.write_text(token)
    return JarvisApi("http://localhost:8765", str(token_path),
                      transport=httpx.MockTransport(handler)), token_path


def test_read_token_strips_whitespace(tmp_path):
    path = tmp_path / "api_token"
    path.write_text("  abc123  \n")

    assert read_token(str(path)) == "abc123"


def test_health_sends_bearer_token_and_returns_json(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True, "modes": {}})

    api, _ = _client(handler, tmp_path)

    result = api.health()

    assert result == {"ok": True, "modes": {}}
    assert seen["auth"] == "Bearer tok-1"


def test_token_is_reread_on_every_call(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"ok": True, "modes": {}})

    api, token_path = _client(handler, tmp_path)

    api.health()
    token_path.write_text("tok-2")
    api.health()

    assert seen == ["Bearer tok-1", "Bearer tok-2"]


def test_non_2xx_raises_api_error_with_status_code(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "hash mismatch"})

    api, _ = _client(handler, tmp_path)

    with pytest.raises(ApiError) as exc_info:
        api.approve("work", "id-1", "a" * 64, "b" * 64)

    assert exc_info.value.status_code == 409
    assert exc_info.value.server_detail == "hash mismatch"  # 409 is a safe-to-carry status
    assert "hash mismatch" not in str(exc_info.value)  # H2: message itself is always fixed


def test_utterance_posts_expected_body(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "ok", "mode_used": "work", "needs_mode": False})

    api, _ = _client(handler, tmp_path)

    result = api.utterance("hello", "work", mode_confirmed=True)

    assert captured["body"] == {"text": "hello", "mode": "work", "mode_confirmed": True}
    assert result["reply"] == "ok"


def test_outbox_returns_items_list(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "personal"
        return httpx.Response(200, json={"items": [{"id": "a"}]})

    api, _ = _client(handler, tmp_path)

    assert api.outbox("personal") == [{"id": "a"}]


def test_422_body_never_reaches_the_exception_message_or_server_detail(tmp_path):
    """H2: a 422 (pydantic validation error) can echo the submitted text back in `detail`. That
    text must never end up in ApiError's message/args, and must never be captured at all (422 is
    not in the safe-to-carry status set)."""
    marker = "XYZZY-SECRET-UTTERANCE-3f9a"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": f"string too long: {marker}"})

    api, _ = _client(handler, tmp_path)

    with pytest.raises(ApiError) as exc_info:
        api.utterance(marker, "work")

    exc = exc_info.value
    assert exc.status_code == 422
    assert exc.server_detail is None
    assert marker not in str(exc)
    assert marker not in repr(exc)
    assert all(marker not in str(arg) for arg in exc.args)


def test_500_body_is_never_captured_either(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error: sensitive stack trace")

    api, _ = _client(handler, tmp_path)

    with pytest.raises(ApiError) as exc_info:
        api.health()

    assert exc_info.value.server_detail is None
    assert "stack trace" not in str(exc_info.value)
