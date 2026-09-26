"""AD4: one daemon per mode. Work runs Slack + work schedules; personal runs Telegram, personal schedules,
the OFW watcher and the Telegram push."""
import pytest
from jarvis import events, main


def test_mode_is_required():
    with pytest.raises(SystemExit):
        main.parse_args([])
    with pytest.raises(SystemExit):
        main.parse_args(["--mode", "both"])
    a = main.parse_args(["--mode", "personal", "--profile", "aws", "--no-channels"])
    assert a.mode == "personal" and a.profile == "aws" and a.no_channels


def test_schedules_filtered_to_the_daemon_mode(modes):
    s = main.schedules({"personal": modes["personal"]}, events.EventBus())
    s.start(paused=True)
    try:
        assert {j.args[1] for j in s.get_jobs()} == {"evening-review", "ofw-check"}
        assert {j.args[0].name for j in s.get_jobs()} == {"personal"}
    finally:
        s.shutdown(wait=False)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, name):
        def fn(*a, **k):
            self.calls.append(name)
            return None
        return fn


@pytest.fixture
def started(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(main.channels, "start_slack", rec("slack"))
    monkeypatch.setattr(main.channels, "start_telegram", rec("telegram"))
    monkeypatch.setattr(main.telegram_push, "start", rec("push"))
    monkeypatch.setattr(main, "_start_watcher", rec("watcher"))
    return rec.calls


@pytest.mark.parametrize("name,expected", [("work", ["slack"]), ("personal", ["telegram", "push", "watcher"])])
def test_start_mode_services(modes, started, name, expected):
    main.start_services(modes[name], {"ofw_watch": {"poll_minutes": 5}}, events.EventBus(), lambda t: t,
                        handle=lambda text, ch: "", channels_on=True)
    assert started == expected


def test_no_channels_still_runs_watcher(modes, started):
    main.start_services(modes["personal"], {"ofw_watch": {}}, events.EventBus(), lambda t: t,
                        handle=lambda text, ch: "", channels_on=False)
    assert started == ["watcher"]


def test_watcher_needs_config_block(modes, started):
    main.start_services(modes["personal"], {}, events.EventBus(), lambda t: t, handle=lambda t, c: "",
                        channels_on=False)
    assert started == []


def test_profile_override_must_not_change_shared_sections():
    base = {"jev": {"a": 1}, "outbox": {"max_age_hours": 24}}
    main.check_shared_sections({**base, "api": {}}, base)
    with pytest.raises(SystemExit, match="JARVIS_DEPLOYMENT"):
        main.check_shared_sections({**base, "jev": {"a": 2}}, base)


@pytest.fixture
def wired(modes, monkeypatch):
    """main() with every thread, socket and file side effect replaced; returns what it passed along."""
    import logging
    from fastapi.testclient import TestClient
    from jarvis import api, brain, heartbeat, logsetup, outbox
    from .conftest import TOKEN
    seen = {"started": [], "shared": []}
    root = logging.getLogger()
    before = list(root.handlers)
    monkeypatch.setattr(main, "load_mode", lambda name, root, cfg: modes[name])
    monkeypatch.setattr(main, "start_services", lambda mode, cfg, bus, redact, handle, channels_on:
                        seen["started"].append((mode.name, channels_on)))
    monkeypatch.setattr(main.events.OutboxPoller, "start", lambda self: None)
    monkeypatch.setattr(heartbeat.Heartbeat, "start", lambda self: None)
    monkeypatch.setattr(main, "schedules", lambda m, bus, cfg: type("S", (), {"start": lambda self: None})())
    monkeypatch.setattr(api, "load_or_create_token", lambda: TOKEN)
    monkeypatch.setattr(api, "share_token", lambda token, deployment: seen["shared"].append(deployment))

    def serve(app, settings):
        seen["settings"] = settings
        with TestClient(app, base_url="http://localhost") as c:
            seen["health"] = c.get("/health", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    monkeypatch.setattr(api, "serve", serve)
    yield seen
    for h in [h for h in root.handlers if h not in before]:
        root.removeHandler(h)
    for wire in (logsetup.set_redactor, outbox.set_redactor, brain.set_redactor):
        wire(None)


def test_main_serves_exactly_one_mode(wired, monkeypatch):
    monkeypatch.delenv("JARVIS_DEPLOYMENT", raising=False)
    main.main(["--mode", "personal", "--no-channels"])
    assert wired["started"] == [("personal", False)]
    assert wired["shared"] == ["local"]
    assert wired["settings"].host == "127.0.0.1" and wired["settings"].port == 8782
    assert wired["health"]["mode"] == "personal" and list(wired["health"]["modes"]) == ["personal"]


def test_main_profile_override(wired, monkeypatch):
    monkeypatch.setattr(main.api, "api_settings",
                        lambda cfg, mode: main.api.resolve_bind({"bind": "loopback"}, mode))
    main.main(["--mode", "work", "--profile", "aws"])
    assert wired["shared"] == ["aws"] and wired["health"]["deployment"] == "aws"
    assert wired["started"] == [("work", True)]
