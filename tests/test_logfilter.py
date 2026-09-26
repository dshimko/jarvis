"""Brief 9.3 / AD11 (tightened in PLAN 3.2): content keys are dropped, noisy loggers pinned, msg redacted,
and under the aws profile an exception carries error_class only."""
import io, json, logging
import pytest
from jarvis import logsetup
from jarvis.logsetup import DROP_KEYS, QUIET_LOGGERS, drop_content, log_event

BRIEF_KEYS = {"body", "text", "draft", "preview", "snippet", "subject", "transcript"}
STRICTER = {"readback", "reply", "prompt", "first_line", "args", "utterance", "message_text"}


@pytest.fixture
def capture():
    """configure() into a StringIO; restores root handlers, levels and the redactor afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_quiet = {n: logging.getLogger(n).level for n in QUIET_LOGGERS}

    def start(mode="personal", fmt="json", deployment="local"):
        buf = io.StringIO()
        logsetup.configure(mode, fmt, deployment=deployment, stream=buf)
        return buf
    yield start
    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
    root.setLevel(saved_level)
    for n, lvl in saved_quiet.items():
        logging.getLogger(n).setLevel(lvl)
    logsetup.set_redactor(None)


def lines(buf):
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_drop_keys_cover_brief_and_stricter_list():
    assert BRIEF_KEYS | STRICTER == set(DROP_KEYS)


def test_drop_content_nested_lists_and_case():
    data = {"Body": "b", "id": "x1", "meta": {"TEXT": "t", "n": 1, "items": [{"Subject": "s", "ok": True}, "plain"]},
            "list": [[{"args": {"to": "a"}}, {"keep": {"PROMPT": "p", "k": 2}}]]}
    assert drop_content(data) == {"id": "x1", "meta": {"n": 1, "items": [{"ok": True}, "plain"]},
                                  "list": [[{}, {"keep": {"k": 2}}]]}
    assert data["Body"] == "b" and data["meta"]["TEXT"] == "t"            # input untouched


def test_event_fields_filtered_and_extra_fields_survive(capture):
    buf = capture()
    log = logging.getLogger("jarvis.test")
    log_event(log, "item_sent", id="w1", duration_ms=12, status="sent", job="j", channel="telegram",
              error_class=None, body="SECRET BODY", nested={"text": "SECRET TEXT", "count": 3})
    (rec,) = lines(buf)
    assert {"ts", "level", "mode", "logger", "event", "msg"} <= set(rec)
    assert rec["event"] == "item_sent" and rec["mode"] == "personal" and rec["logger"] == "jarvis.test"
    assert rec["id"] == "w1" and rec["duration_ms"] == 12 and rec["status"] == "sent"
    assert rec["job"] == "j" and rec["channel"] == "telegram" and rec["nested"] == {"count": 3}
    assert "SECRET" not in buf.getvalue() and "body" not in rec


def test_plain_extra_is_filtered_case_insensitively(capture):
    buf = capture()
    logging.getLogger("jarvis.x").warning("fixed text", extra={"Subject": "SECRET SUBJ", "status": "ok",
                                                               "draft": {"a": 1}})
    (rec,) = lines(buf)
    assert rec["msg"] == "fixed text" and rec["status"] == "ok" and rec["level"] == "WARNING"
    assert "SECRET" not in buf.getvalue() and "draft" not in rec


def test_one_valid_json_object_per_line(capture):
    buf = capture()
    log = logging.getLogger("jarvis.y")
    log.info("line one\nline two")
    log.info("unicode ✓ \"quoted\"")
    log_event(log, "heartbeat")
    raw = buf.getvalue().splitlines()
    assert len(raw) == 3 and all(isinstance(json.loads(line), dict) for line in raw)


@pytest.mark.parametrize("name", QUIET_LOGGERS)
def test_third_party_info_dropped(capture, name):
    buf = capture()
    logging.getLogger(name).info("HTTP Request: POST https://api.telegram.org/bot123:SECRET/getUpdates")
    logging.getLogger(f"{name}.sub").info("child too")
    assert buf.getvalue() == ""
    logging.getLogger(name).warning("warn passes")
    assert lines(buf)[0]["msg"] == "warn passes"


def test_quiet_list_is_the_amended_set():
    assert set(QUIET_LOGGERS) >= {"httpx", "httpcore", "telegram", "slack_bolt", "slack_sdk", "mcp", "urllib3",
                                  "googleapiclient"}


def test_msg_is_redacted(capture):
    buf = capture()
    logsetup.set_redactor(lambda t: t.replace("tok-secret-123", "[redacted]"))
    logging.getLogger("jarvis.z").warning("url %s", "https://x/bottok-secret-123/")
    assert "tok-secret-123" not in buf.getvalue() and "[redacted]" in lines(buf)[0]["msg"]


def _raise_and_log(log):
    try:
        raise ValueError("args contained ofw-body-SECRET")
    except ValueError:
        log.exception("send failed")


def test_aws_exception_has_error_class_only(capture):
    buf = capture(deployment="aws")
    _raise_and_log(logging.getLogger("jarvis.outbox"))
    (rec,) = lines(buf)
    assert rec["error_class"] == "ValueError" and rec["msg"] == "send failed"
    text = buf.getvalue()
    assert "Traceback" not in text and "SECRET" not in text
    assert not {"exc_info", "exc_text", "stack_info", "traceback"} & set(rec)


def test_aws_stack_info_dropped(capture):
    buf = capture(deployment="aws")
    logging.getLogger("jarvis.s").warning("with stack", stack_info=True)
    assert "Stack" not in buf.getvalue() and "stack_info" not in lines(buf)[0]


def test_local_json_keeps_redacted_traceback(capture):
    buf = capture(deployment="local")
    logsetup.set_redactor(lambda t: t.replace("SECRET", "[redacted]"))
    _raise_and_log(logging.getLogger("jarvis.outbox"))
    (rec,) = lines(buf)
    assert rec["error_class"] == "ValueError" and "Traceback" in rec["exc_text"]
    assert "SECRET" not in buf.getvalue()


def test_aws_forces_json_even_if_text_requested(capture):
    buf = capture(fmt="text", deployment="aws")
    logging.getLogger("jarvis.t").info("hello")
    assert lines(buf)[0]["msg"] == "hello"


def test_text_format_for_local_dev(capture, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_FORMAT", "text")
    buf = capture(fmt=None)
    log_event(logging.getLogger("jarvis.t"), "item_sent", id="w1", body="SECRET")
    out = buf.getvalue()
    assert "INFO jarvis.t: item_sent" in out and "id=w1" in out and "SECRET" not in out


def test_configure_is_idempotent(capture):
    capture()
    capture()
    ours = [h for h in logging.getLogger().handlers if getattr(h, logsetup.HANDLER_MARK, False)]
    assert len(ours) == 1 and any(isinstance(f, logsetup.ContentFilter) for f in ours[0].filters)
