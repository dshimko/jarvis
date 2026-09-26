"""AD34/AD35: the OFW write token is executor-only, and the executor maps ofw result statuses."""
import json, logging, subprocess
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from jarvis import api, brain, outbox
from jarvis.events import EventBus
from jarvis.handler import make_handler
from jarvis.modes import EXECUTOR_ONLY_KEYS, make_redactor
from .conftest import TOKEN, FakeResult, approve_ok, make_item, meta_of

WRITE = "ofw-write-secret-5555"
READ = "ofw-personal-secret-4444"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Text:
    type = "text"

    def __init__(self, text):
        self.text = text


def without_write(mode):
    return replace(mode, env={k: v for k, v in mode.env.items() if k != "OFW_MCP_WRITE_TOKEN"})


def approved(mode, item_id="o1"):
    make_item(mode, item_id)
    approve_ok(mode, item_id)
    return outbox.item_path(mode, item_id)


def result(structured=None, content="ok", is_error=False):
    r = FakeResult(is_error=is_error, content=content, structured={})
    r.structuredContent = structured
    return r


# ---- (a) never in a claude -p env ----

def test_write_token_is_executor_only(modes):
    p = modes["personal"]
    assert EXECUTOR_ONLY_KEYS == frozenset({"OFW_MCP_WRITE_TOKEN"})
    assert p.env["OFW_MCP_WRITE_TOKEN"] == WRITE
    env = p.subprocess_env()
    assert "OFW_MCP_WRITE_TOKEN" not in env and WRITE not in env.values()
    assert env["OFW_MCP_TOKEN"] == READ


def test_redactor_still_covers_write_token(modes):
    assert make_redactor({"personal": modes["personal"]})(f"x {WRITE} y") == "x [redacted] y"


def test_ask_detailed_env_lacks_write_token(modes, monkeypatch):
    seen = []

    def fake_run(cmd, cwd, env, timeout):
        seen.append(env)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": "done"}), stderr="")
    monkeypatch.setattr(brain, "_run", fake_run)
    assert brain.ask_detailed(modes["personal"], "hello").ok
    (env,) = seen
    assert "OFW_MCP_WRITE_TOKEN" not in env and WRITE not in env.values()
    assert env["OFW_MCP_TOKEN"] == READ


# ---- (b), (c) _server_conf ----

def test_server_conf_write_uses_write_token_for_ofw_only(modes):
    p = modes["personal"]
    ofw = outbox._server_conf(p, "ofw", write=True)
    assert ofw["headers"]["Authorization"] == f"Bearer {WRITE}"
    assert ofw["url"] == "https://ofw.example.test/mcp"
    gmail = outbox._server_conf(p, "gmail", write=True)
    assert gmail == outbox._server_conf(p, "gmail", write=False)
    assert gmail["headers"]["Authorization"] == "Bearer gmail-personal-secret-3333"


def test_server_conf_read_never_carries_write_token(modes):
    conf = outbox._server_conf(modes["personal"], "ofw", write=False)
    assert conf["headers"]["Authorization"] == f"Bearer {READ}"
    assert WRITE not in json.dumps(conf)


@pytest.mark.parametrize("value", [None, ""])
def test_server_conf_write_without_token_blocks(modes, value):
    p = without_write(modes["personal"])
    if value is not None:
        p = replace(p, env={**p.env, "OFW_MCP_WRITE_TOKEN": value})
    with pytest.raises(outbox.Blocked, match="^ofw write token not configured$"):
        outbox._server_conf(p, "ofw", write=True)
    assert outbox._server_conf(p, "ofw", write=False)["headers"]["Authorization"] == f"Bearer {READ}"


# ---- (d) executor: missing token blocks, no MCP call ----

def test_execute_without_write_token_blocks(modes, jev_scores, mcp_calls):
    p = without_write(modes["personal"])
    path = approved(p)
    out = outbox.execute_detailed(p, "o1")
    assert not out.sent and out.reason == "ofw write token not configured"
    meta = meta_of(path)
    assert meta["status"] == "approved" and meta["last_block"] == "ofw write token not configured"
    assert not mcp_calls


def test_execute_sends_with_write_token(modes, jev_scores, mcp_calls):
    approved(modes["personal"])
    assert outbox.execute_detailed(modes["personal"], "o1").sent
    (call,) = mcp_calls
    assert call["conf"]["headers"]["Authorization"] == f"Bearer {WRITE}"


# ---- (e) the API redacts the write token ----

def test_api_redacts_write_token(modes, monkeypatch):
    def leak(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"error: token {WRITE}")
    monkeypatch.setattr(brain.subprocess, "run", leak)
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    with TestClient(app, base_url="http://localhost") as c:
        r = c.post("/utterance", json={"text": "hello", "mode": "personal"}, headers=AUTH)
    assert r.status_code == 200
    assert WRITE not in r.text and "[redacted]" in r.json()["reply"]


# ---- (f) ofw status mapping ----

def _run(modes, mcp_calls, res, caplog):
    m = modes["personal"]
    path = approved(m)
    mcp_calls.result = res
    with caplog.at_level(logging.INFO, logger="jarvis.outbox"):
        out = outbox.execute_detailed(m, "o1")
    return out, meta_of(path)


def _events(caplog, name):
    return [r.jarvis_fields for r in caplog.records if getattr(r, "jarvis_event", None) == name]


def test_ofw_sent(modes, jev_scores, mcp_calls, caplog):
    out, meta = _run(modes, mcp_calls, result({"status": "sent", "ofw_id": "9"}), caplog)
    assert out.sent and meta["status"] == "sent" and meta.get("sent_at") and "sent_note" not in meta
    assert not _events(caplog, "send_unverified")


@pytest.mark.parametrize("status", ["sent_unverified", "duplicate"])
def test_ofw_unverified_is_sent_with_note(modes, jev_scores, mcp_calls, caplog, status):
    out, meta = _run(modes, mcp_calls, result({"status": status}), caplog)
    assert out.sent and meta["status"] == "sent" and meta["sent_note"] == status
    assert _events(caplog, "send_unverified") == [{"id": "o1", "status": status}]


def test_ofw_status_from_first_text_block(modes, jev_scores, mcp_calls, caplog):
    blocks = [Text(json.dumps({"status": "duplicate", "ofw_id": "9"})), Text("ignored")]
    out, meta = _run(modes, mcp_calls, result(None, blocks), caplog)
    assert out.sent and meta["sent_note"] == "duplicate"


@pytest.mark.parametrize("structured,content,reason", [
    ({"status": "writes_disabled"}, "ok", "writes_disabled"),
    ({"status": "rate_limited", "retry_after_s": 60}, "ok", "rate_limited"),
    ({"status": "error", "error_class": "OFWLayoutChanged"}, "ok", "error"),
    (None, [Text("not json at all")], "no status"),
    (None, [Text(json.dumps({"no": "status"}))], "no status"),
    (None, [Text(json.dumps(["status"]))], "no status"),
    (None, [], "no status"),
    (None, "ok", "no status"),
    ({"status": 7}, "ok", "no status"),
    ({"status": "Free text: with words that are content"}, "ok", "no status"),
    ({"status": "sent\n"}, "ok", "no status"),
    ({"status": "duplicate\n"}, "ok", "no status"),
    ({"status": "queued"}, "ok", "no status"),               # a plain word outside the AD35 vocabulary
])
def test_ofw_other_statuses_fail(modes, jev_scores, mcp_calls, caplog, structured, content, reason):
    out, meta = _run(modes, mcp_calls, result(structured, content), caplog)
    assert not out.sent and meta["status"] == "failed" and meta["last_block"] == reason
    assert out.reason == reason and "sent_at" not in meta


def test_ofw_is_error_fails_without_content(modes, jev_scores, mcp_calls, caplog):
    out, meta = _run(modes, mcp_calls, result(None, [Text("server said something long")], True), caplog)
    assert not out.sent and meta["status"] == "failed" and meta["last_block"] == "no status"
    assert "something long" not in json.dumps(meta) and "something long" not in out.message


def test_non_ofw_servers_ignore_status(modes, jev_scores, mcp_calls):
    m = modes["personal"]
    path = make_item(m, "g1", server="gmail", tool="send_message", args={"to": "a@example.test"})
    approve_ok(m, "g1")
    mcp_calls.result = FakeResult(structured={"status": "writes_disabled"})
    assert outbox.execute_detailed(m, "g1").sent
    meta = meta_of(path)
    assert meta["status"] == "sent" and "sent_note" not in meta
    assert mcp_calls[0]["conf"]["headers"]["Authorization"] == "Bearer gmail-personal-secret-3333"
