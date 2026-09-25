"""Brief 10.2: /outbox + /approve. Hash mismatch rejected; a hash match still runs every gate; expired stays blocked."""
import json
import pytest
from fastapi.testclient import TestClient
from jarvis import api, jev, ledger, outbox
from jarvis.events import EventBus
from jarvis.handler import make_handler
from .conftest import TOKEN, FakeResult, make_item, meta_of, now_iso
import datetime as dt

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(modes):
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def listed(client, mode):
    r = client.get(f"/outbox?mode={mode}", headers=AUTH)
    assert r.status_code == 200
    return {i["id"]: i for i in r.json()["items"]}


def approve(client, mode, item, **extra):
    body = {"mode": mode, "id": item["id"], "body_sha256": item["body_sha256"],
            "readback_sha256": item["readback_sha256"], "reconfirm": False, **extra}
    return client.post("/approve", json=body, headers=AUTH)


def test_outbox_item_shape(client, modes):
    make_item(modes["work"], "w1", body="See you at 5\nthanks")
    make_item(modes["work"], "done", status="sent")
    items = listed(client, "work")
    assert set(items) == {"w1"}
    it = items["w1"]
    assert set(it) == {"id", "mode", "status", "preview", "first_line", "body_sha256", "readback", "readback_sha256",
                       "obsidian_uri", "windows_path", "last_block", "tone_flagged"}
    assert it["first_line"] == "See you at 5" and it["preview"] == "See you at 5\nthanks"
    assert it["readback"] == 'mode: work\nserver: slack\ntool: post_message\nchannel: "C123"\ntext: "See you at 5"'
    assert it["readback_sha256"] == outbox.sha256(it["readback"])
    assert it["body_sha256"] == outbox.sha256(it["preview"])
    assert it["obsidian_uri"] == "obsidian://open?vault=Jarvis-Work&file=outbox%2Fw1.md"
    assert it["tone_flagged"] is False


def test_outbox_mode_validated(client):
    assert client.get("/outbox?mode=other", headers=AUTH).status_code == 422


def test_approve_sends_exact_args(client, modes, jev_scores, mcp_calls):
    p = make_item(modes["work"], "ok1")
    r = approve(client, "work", listed(client, "work")["ok1"])
    assert r.status_code == 200 and r.json() == {"result": "Sent ok1.", "sent": True, "blocked": None}
    assert mcp_calls[0]["tool"] == "post_message"
    assert mcp_calls[0]["args"] == {"channel": "C123", "text": "See you at 5"}
    assert meta_of(p)["status"] == "sent"


def test_body_edit_after_listing_rejected(client, modes, jev_scores, mcp_calls):
    p = make_item(modes["work"], "e1")
    item = listed(client, "work")["e1"]
    meta, _ = outbox.read_item(p)
    outbox.write_item(p, meta, "Different text")
    assert approve(client, "work", item).status_code == 409
    assert not mcp_calls and meta_of(p)["status"] == "pending"


def test_args_edit_after_listing_rejected(client, modes, jev_scores, mcp_calls):
    """D12: same body, different args -> readback hash mismatch."""
    p = make_item(modes["work"], "e2")
    item = listed(client, "work")["e2"]
    meta, body = outbox.read_item(p)
    outbox.write_item(p, {**meta, "args": json.dumps({"channel": "C999", "text": "See you at 5"})}, body)
    assert approve(client, "work", item).status_code == 409
    assert not mcp_calls


def test_hash_match_disallowed_tool_blocked(client, modes, jev_scores, mcp_calls):
    make_item(modes["work"], "g1", tool="delete_channel")
    r = approve(client, "work", listed(client, "work")["g1"])
    assert r.status_code == 200 and r.json()["sent"] is False
    assert "not an allowed write tool" in r.json()["blocked"]
    assert not mcp_calls


def test_hash_match_daily_cap_blocked(client, modes, jev_scores, mcp_calls):
    m = modes["personal"]
    for i in range(m.daily_write_cap):
        ledger.record_attempt("personal", f"s{i}")
    make_item(m, "capped")
    r = approve(client, "personal", listed(client, "personal")["capped"])
    assert r.json() == {"result": "Blocked capped: daily cap reached", "sent": False, "blocked": "daily cap reached"}
    assert not mcp_calls


def test_hash_match_tone_flag_then_reconfirm(client, modes, jev_scores, mcp_calls):
    jev_scores["hostile"] = 0.9
    make_item(modes["personal"], "tone")
    r = approve(client, "personal", listed(client, "personal")["tone"])
    assert r.json()["sent"] is False and r.json()["blocked"].startswith("tone flag")
    item = listed(client, "personal")["tone"]
    assert item["tone_flagged"] is True
    r2 = approve(client, "personal", item, reconfirm=True)
    assert r2.json()["sent"] is True and len(mcp_calls) == 1


def test_preemptive_reconfirm_400(client, modes, jev_scores, mcp_calls):
    """D5: reconfirm before any tone flag is refused."""
    p = make_item(modes["personal"], "pre")
    r = approve(client, "personal", listed(client, "personal")["pre"], reconfirm=True)
    assert r.status_code == 400
    assert meta_of(p)["status"] == "pending" and not mcp_calls


def test_hash_match_leak_blocked(client, modes, jev_scores, mcp_calls):
    jev_scores["leak"] = 0.9
    make_item(modes["work"], "leak")
    assert approve(client, "work", listed(client, "work")["leak"]).json()["blocked"] == "possible cross-mode content"
    assert not mcp_calls


def test_expired_stays_blocked_with_correct_hash(client, modes, jev_scores, mcp_calls):
    p = make_item(modes["work"], "old", created=now_iso(-dt.timedelta(hours=30)))
    item = listed(client, "work")["old"]
    r = approve(client, "work", item)
    assert r.json() == {"result": "Blocked old: expired", "sent": False, "blocked": "expired"}
    assert meta_of(p)["status"] == "expired"
    assert approve(client, "work", item).status_code == 409
    assert not mcp_calls


def test_unknown_and_invalid_ids(client, modes):
    fake = {"id": "nope", "body_sha256": "0" * 64, "readback_sha256": "0" * 64}
    assert approve(client, "work", fake).status_code == 404
    assert approve(client, "work", {**fake, "id": "../etc/passwd"}).status_code == 422
    assert approve(client, "work", {**fake, "body_sha256": "xyz"}).status_code == 422


def test_personal_item_cannot_be_approved_with_mode_work(client, modes, jev_scores, mcp_calls):
    p = make_item(modes["personal"], "pers")
    item = listed(client, "personal")["pers"]
    assert approve(client, "work", item).status_code == 404
    (modes["work"].vault / "outbox" / "pers.md").write_bytes(p.read_bytes())
    assert approve(client, "work", item).status_code == 409
    assert not mcp_calls
    assert meta_of(p)["status"] == "pending"


def test_unexpected_exception_fails_closed(client, modes, mcp_calls, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("jev down and haiku down")
    monkeypatch.setattr(jev, "decide", boom)
    make_item(modes["work"], "ex")
    r = approve(client, "work", listed(client, "work")["ex"])
    assert r.status_code == 200
    assert r.json() == {"result": "internal error", "sent": False, "blocked": "internal error"}
    assert not mcp_calls


def test_server_error_redacted(client, modes, jev_scores, mcp_calls):
    """C3: an MCP error echoing a secret never reaches Windows."""
    mcp_calls.result = FakeResult(is_error=True, content="bad header Bearer xoxp-work-secret-1111")
    make_item(modes["work"], "red")
    body = approve(client, "work", listed(client, "work")["red"]).text
    assert "xoxp-work-secret-1111" not in body and "[redacted]" in body


def test_vault_tone_flag_is_not_trusted(client, modes, jev_scores, mcp_calls):
    """M2: last_block written into the file does not make an item tone-flagged or allow reconfirm."""
    p = make_item(modes["personal"], "fake", status="approved", last_block="tone flag: reread")
    assert "fake" not in listed(client, "personal")
    meta, body = outbox.read_item(p)
    item = {"id": "fake", "body_sha256": outbox.body_sha256(body), "readback_sha256": outbox.readback_sha256(meta)}
    assert approve(client, "personal", item, reconfirm=True).status_code == 400
    assert not mcp_calls


def test_vault_sent_fields_do_not_drive_cap(client, modes, jev_scores, mcp_calls):
    """L3: the cap comes from the daemon ledger, so an agent can't fake or erase sends in the vault."""
    m = modes["personal"]
    for i in range(m.daily_write_cap):
        make_item(m, f"s{i}", status="sent", sent_at=now_iso())
    make_item(m, "fine")
    assert approve(client, "personal", listed(client, "personal")["fine"]).json()["sent"] is True
    assert ledger.attempts_today("personal") == 1


def test_bad_item_skipped_not_500(client, modes, monkeypatch):
    make_item(modes["work"], "good")
    make_item(modes["work"], "bad")
    real = api.item_view

    def flaky(mode, meta, body, path):
        if meta["id"] == "bad":
            raise RuntimeError("boom")
        return real(mode, meta, body, path)
    monkeypatch.setattr(api, "item_view", flaky)
    assert set(listed(client, "work")) == {"good"}


def test_non_string_arg_keys_listed_safely(client, modes):
    p = modes["work"].vault / "outbox" / "weird.md"
    p.write_text("---\nid: weird\nmode: work\nserver: slack\ntool: post_message\nargs: {1: x}\n"
                 f"status: pending\ncreated: '{now_iso()}'\n---\nhi\n", encoding="utf-8")
    item = listed(client, "work")["weird"]
    assert "unparseable" in item["readback"]
    assert approve(client, "work", item).status_code == 409
