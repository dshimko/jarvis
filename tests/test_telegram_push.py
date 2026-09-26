"""AD10: the personal daemon pushes a one-line notice for each new draft; the body stays in `read <id>`."""
import logging
from dataclasses import replace
import pytest
from jarvis import events, telegram_push

BOT = "123456:BOT-TOKEN-SECRET"


@pytest.fixture
def personal(modes):
    m = modes["personal"]
    return replace(m, env={**m.env, "TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_OWNER_CHAT_ID": "4242"})


class Calls(list):
    error = None
    fn = None


@pytest.fixture
def posts():
    calls = Calls()

    def post(url, **kw):
        calls.append((url, kw))
        if calls.error:
            raise calls.error
    calls.error = None
    calls.fn = post
    return calls


def push(mode, posts, redact=lambda t: t):
    return telegram_push.TelegramPush(mode, redact, post=posts.fn, background=False)


def test_pending_new_sends_one_line_notice(personal, posts):
    p = push(personal, posts)
    p.on_event("pending_new", {"mode": "personal", "id": "p1", "first_line": "Pickup at 5 works.",
                               "obsidian_uri": "obsidian://x"})
    ((url, kw),) = posts
    assert url == f"https://api.telegram.org/bot{BOT}/sendMessage"
    assert kw["json"] == {"chat_id": 4242,
                          "text": "New draft `p1`: `Pickup at 5 works.`. Send `read p1` to review."}
    assert kw["timeout"] == telegram_push.SEND_TIMEOUT_SECONDS


def test_other_events_and_modes_ignored(personal, posts):
    p = push(personal, posts)
    p.on_event("item_sent", {"mode": "personal", "id": "p1"})
    p.on_event("pending_new", {"mode": "work", "id": "w1", "first_line": "x"})
    assert not posts


def test_first_line_redacted_and_truncated(personal, posts):
    p = push(personal, posts, redact=lambda t: t.replace("ofw-secret-1", "[redacted]"))
    p.on_event("pending_new", {"mode": "personal", "id": "p2", "first_line": "ofw-secret-1 " + "x" * 500})
    text = posts[0][1]["json"]["text"]
    assert "ofw-secret-1" not in text and "[redacted]" in text
    assert len(text) < telegram_push.MAX_FIRST_LINE + 80


def test_send_error_logs_error_class_only(personal, posts, caplog):
    posts.error = RuntimeError(f"connect failed for https://api.telegram.org/bot{BOT}/sendMessage")
    with caplog.at_level(logging.INFO, logger="jarvis.telegram_push"):
        push(personal, posts).on_event("pending_new", {"mode": "personal", "id": "p3", "first_line": "hi"})
    (rec,) = [r for r in caplog.records if getattr(r, "jarvis_event", None) == "telegram_push_error"]
    assert rec.jarvis_fields == {"error_class": "RuntimeError", "id": "p3"}
    assert BOT not in caplog.text


def test_start_requires_tokens_and_subscribes(modes, personal, posts):
    bus = events.EventBus()
    assert telegram_push.start(modes["personal"], bus, lambda t: t) is None      # empty tokens: skipped
    p = telegram_push.start(personal, bus, lambda t: t, post=posts.fn, background=False)
    assert p is not None
    bus.publish("pending_new", {"mode": "personal", "id": "p4", "first_line": "hi"})
    assert len(posts) == 1


def test_bad_owner_chat_id_skips(personal):
    bad = replace(personal, env={**personal.env, "TELEGRAM_OWNER_CHAT_ID": "not-a-number"})
    assert telegram_push.start(bad, events.EventBus(), lambda t: t) is None


def test_bus_listener_errors_do_not_break_publish(caplog):
    bus = events.EventBus()
    got = []
    bus.add_listener(lambda kind, data: 1 / 0)
    bus.add_listener(lambda kind, data: got.append((kind, data)))
    with caplog.at_level(logging.ERROR, logger="jarvis.events"):
        bus.publish("schedule_done", {"mode": "personal", "job": "j", "ok": True})
    assert got == [("schedule_done", {"mode": "personal", "job": "j", "ok": True})]
    rec = [r for r in caplog.records if getattr(r, "jarvis_event", None) == "bus_listener_error"][0]
    assert rec.jarvis_fields == {"error_class": "ZeroDivisionError"}


class Resp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.mark.parametrize("status", [401, 429, 500])
def test_non_2xx_logs_status_not_url(personal, caplog, status):
    sent = []

    def post(url, **kw):
        sent.append(url)
        return Resp(status)
    with caplog.at_level(logging.INFO, logger="jarvis.telegram_push"):
        telegram_push.TelegramPush(personal, lambda t: t, post=post, background=False).on_event(
            "pending_new", {"mode": "personal", "id": "p5", "first_line": "hi"})
    (rec,) = [r for r in caplog.records if getattr(r, "jarvis_event", None) == "telegram_push_error"]
    assert rec.jarvis_fields == {"error_class": "HTTPStatus", "status": status, "id": "p5"}
    assert BOT not in caplog.text


def test_2xx_is_quiet(personal, caplog):
    with caplog.at_level(logging.INFO, logger="jarvis.telegram_push"):
        telegram_push.TelegramPush(personal, lambda t: t, post=lambda url, **kw: Resp(200),
                                   background=False).on_event("pending_new", {"mode": "personal", "id": "p6"})
    assert not [r for r in caplog.records if getattr(r, "jarvis_event", None) == "telegram_push_error"]
