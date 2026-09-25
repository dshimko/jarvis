"""handler: D6, D7, D8, text-channel binding."""
import pytest
from jarvis import brain, jev, outbox, router
from jarvis.handler import make_handler, normalize, VOICE_APPROVE_REFUSAL
from .conftest import make_item, meta_of


@pytest.fixture
def no_brain(monkeypatch):
    calls = []
    monkeypatch.setattr(brain, "ask", lambda mode, text, **kw: calls.append((mode.name, text)) or "brain reply")
    return calls


@pytest.fixture
def jev_explodes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Jev must not be consulted")
    monkeypatch.setattr(jev, "decide", boom)


@pytest.mark.parametrize("text", ["What's pending?", "what’s pending", "PENDING.", "  pending  ", "Outbox!",
                                  "work, what's pending?"])
def test_voice_pending_uses_bound_mode_without_jev(modes, jev_explodes, no_brain, text):
    make_item(modes["work"], "w-item")
    make_item(modes["personal"], "p-item")
    handle_detailed, _ = make_handler(modes)
    r = handle_detailed(text, "voice", voice_mode="work")
    assert r.text == "Pending: w-item" and r.mode == "work" and not r.needs_mode
    assert not no_brain


def test_read_returns_readback_and_code(modes, jev_explodes, no_brain):
    p = make_item(modes["personal"], "Pick-Up", body="Pickup at 5 works.")
    handle_detailed, _ = make_handler(modes)
    text = handle_detailed("Read Pick-Up.", "voice", voice_mode="personal").text
    assert text == outbox.read_card(modes["personal"], "Pick-Up")
    assert text.endswith(f"code {outbox.readback_code(meta_of(p))}") and 'body: "Pickup at 5 works."' in text
    assert handle_detailed("read Pick-Up", "voice", voice_mode="work").text == "No approvable item with that id."


@pytest.mark.parametrize("text", ["approve w1", "Approve w1.", "send w1 reconfirm", "work, approve w1"])
def test_voice_approve_refused(modes, jev_explodes, no_brain, text):
    p = make_item(modes["work"], "w1")
    handle_detailed, _ = make_handler(modes)
    r = handle_detailed(text, "voice", voice_mode="work")
    assert r.text == VOICE_APPROVE_REFUSAL
    assert meta_of(p)["status"] == "pending"


def test_voice_approve_refused_even_after_mode_confirm(modes, jev_scores, no_brain):
    p = make_item(modes["personal"], "p1")
    handle_detailed, _ = make_handler(modes)
    assert handle_detailed("personal, approve p1", "voice", voice_mode="work").needs_mode
    r = handle_detailed("personal, approve p1", "voice", voice_mode="personal", mode_confirmed=True)
    assert r.text == VOICE_APPROVE_REFUSAL and meta_of(p)["status"] == "pending"


def test_slack_text_approve_needs_code(modes, jev_scores, mcp_calls, no_brain):
    p = make_item(modes["work"], "w2")
    _, handle = make_handler(modes)
    assert handle("approve w2", "slack_work").startswith("Not approved. Send `read w2` first")
    assert "wrong code" in handle("approve w2 0badc0de", "slack_work")
    assert meta_of(p)["status"] == "pending" and not mcp_calls
    code = handle("read w2", "slack_work").rsplit("code ", 1)[1]
    assert handle(f"approve w2 {code}", "slack_work") == "Sent w2."
    assert meta_of(p)["status"] == "sent" and len(mcp_calls) == 1


def test_code_goes_stale_when_args_change(modes, jev_scores, mcp_calls, no_brain):
    p = make_item(modes["work"], "w3")
    _, handle = make_handler(modes)
    code = handle("read w3", "slack_work").rsplit("code ", 1)[1]
    meta, body = outbox.read_item(p)
    outbox.write_item(p, {**meta, "args": '{"channel": "C999", "text": "See you at 5"}'}, body)
    assert "wrong code" in handle(f"approve w3 {code}", "slack_work")
    assert not mcp_calls


def test_telegram_reconfirm_with_code(modes, jev_scores, mcp_calls, no_brain):
    make_item(modes["personal"], "p9")
    jev_scores["hostile"] = 0.9
    _, handle = make_handler(modes)
    code = handle("read p9", "telegram").rsplit("code ", 1)[1]
    assert "tone flag" in handle(f"approve p9 {code}", "telegram")
    assert handle(f"approve p9 {code} reconfirm", "telegram") == "Sent p9."


def test_text_reconfirm_without_tone_flag_refused(modes, jev_scores, mcp_calls, no_brain):
    """D5 applies to text channels too: reconfirm needs a real daemon-side tone flag first."""
    p = make_item(modes["work"], "w9")
    _, handle = make_handler(modes)
    code = handle("read w9", "slack_work").rsplit("code ", 1)[1]
    reply = handle(f"approve w9 {code} reconfirm", "slack_work")
    assert reply == ("reconfirm is only accepted after the tone check flags an item; "
                     "approve without reconfirm first")
    assert meta_of(p)["status"] == "pending" and not mcp_calls


def test_text_approve_errors_are_replies(modes, jev_scores, mcp_calls, no_brain):
    _, handle = make_handler(modes)
    assert handle("approve missing 0badc0de", "slack_work") == "Not approved missing: no such item"
    make_item(modes["work"], "sent1", status="sent")
    assert "not approvable" in handle("approve sent1 0badc0de", "slack_work")


def test_channels_are_mode_bound(modes, jev_scores, no_brain):
    _, handle = make_handler(modes)
    handle("personal, check ofw", "slack_work")
    handle("work, check slack", "telegram")
    assert no_brain == [("work", "personal, check ofw"), ("personal", "work, check slack")]


def test_unknown_channel_asks(modes, jev_scores, no_brain):
    handle_detailed, _ = make_handler(modes)
    assert handle_detailed("hi", "email").needs_mode
    assert handle_detailed("hi", "voice", voice_mode=None).needs_mode
    assert not no_brain


def test_jev_unclear_uses_hotkey(modes, jev_scores, no_brain):
    handle_detailed, _ = make_handler(modes)
    r = handle_detailed("what's on today", "voice", voice_mode="personal")
    assert r.mode == "personal" and no_brain == [("personal", "what's on today")]


def test_router_prefix():
    assert router.spoken_prefix("Personal, hi") == "personal"
    assert router.spoken_prefix("jarvis work what's up") == "work"
    assert router.spoken_prefix("workshop notes") is None
    assert router.strip_prefix("work, pending?") == "pending?"


def test_normalize():
    assert normalize("  What’s   pending?! ") == "What's pending"
