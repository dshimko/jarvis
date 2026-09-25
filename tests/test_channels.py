"""D10: a channel with empty tokens is skipped with a warning, never crashes the daemon."""
import logging
from jarvis import channels


def test_empty_tokens_skip(modes, caplog, monkeypatch):
    monkeypatch.setattr(channels, "App", lambda **k: (_ for _ in ()).throw(AssertionError("must not build")))
    with caplog.at_level(logging.WARNING):
        assert channels.start_slack(modes["work"], lambda *a: "") is False
        assert channels.start_telegram(modes["personal"], lambda *a: "") is False
    assert "Slack channel skipped" in caplog.text and "Telegram channel skipped" in caplog.text
    assert "xoxp" not in caplog.text


def test_bad_telegram_chat_id_skips(modes, caplog):
    from dataclasses import replace
    m = replace(modes["personal"], env={"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_OWNER_CHAT_ID": "me"})
    assert channels.start_telegram(m, lambda *a: "") is False


def test_slack_init_failure_skips(modes, monkeypatch):
    from dataclasses import replace
    def fail(**k):
        raise RuntimeError("invalid_auth")
    monkeypatch.setattr(channels, "App", fail)
    m = replace(modes["work"], env={"SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "SLACK_OWNER_USER_ID": "U1"})
    assert channels.start_slack(m, lambda *a: "") is False
