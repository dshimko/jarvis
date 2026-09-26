"""AD10: the OFW Gmail watcher reads message ids only, dedups locally, and logs metadata only."""
import json, logging, os, stat
import pytest
from jarvis import brain, gmail_watch
from jarvis.gmail_watch import Watcher

QUERY = "from:@ourfamilywizard.com is:unread newer_than:2d"
CFG = {"poll_minutes": 5, "query": QUERY}


class FakeRequest:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeMessages:
    def __init__(self, svc):
        self.svc = svc

    def list(self, **kw):
        self.svc.list_calls.append(kw)

        def run():
            if self.svc.error:
                raise self.svc.error
            return {"messages": [{"id": i, "threadId": "t"} for i in self.svc.ids]} if self.svc.ids else {}
        return FakeRequest(run)

    def get(self, **kw):
        self.svc.get_calls.append(kw)
        return FakeRequest(lambda: {"id": kw.get("id"), "internalDate": "1"})


class FakeUsers:
    def __init__(self, svc):
        self.svc = svc

    def messages(self):
        return FakeMessages(self.svc)

    def getProfile(self, userId):
        return FakeRequest(lambda: {"emailAddress": "owner@example.test"})


class FakeService:
    def __init__(self, ids=()):
        self.ids, self.error = list(ids), None
        self.list_calls, self.get_calls = [], []

    def users(self):
        return FakeUsers(self)


class ApiError(Exception):
    pass


class Calls(list):
    fn = None


@pytest.fixture
def asks(monkeypatch):
    calls = Calls()

    def ask(mode, prompt, **kw):
        calls.append((mode.name, prompt))
        return brain.Answer("drafted", ask.ok)
    ask.ok = True
    calls.fn = ask
    return calls


def watcher(modes, home, svc, asks):
    return Watcher(modes["personal"], CFG, ask=asks.fn, service_factory=lambda: svc)


def test_new_ids_trigger_exactly_one_check(modes, home, asks):
    svc = FakeService(["m1", "m2"])
    w = watcher(modes, home, svc, asks)
    assert w.poll_once() == 2
    assert asks == [("personal", "/ofw-check")]
    seen = home / ".jarvis" / "ofw_watch_seen.json"
    assert set(json.loads(seen.read_text())) == {"m1", "m2"}
    assert stat.S_IMODE(seen.stat().st_mode) == 0o600


def test_seen_ids_do_not_trigger_and_survive_restart(modes, home, asks):
    svc = FakeService(["m1"])
    watcher(modes, home, svc, asks).poll_once()
    w2 = watcher(modes, home, svc, asks)
    assert w2.poll_once() == 0 and len(asks) == 1
    svc.ids.append("m3")
    assert w2.poll_once() == 1 and len(asks) == 2


def test_no_ids_no_check(modes, home, asks):
    assert watcher(modes, home, FakeService([]), asks).poll_once() == 0
    assert not asks


def test_bodies_never_requested(modes, home, asks):
    svc = FakeService(["m1"])
    watcher(modes, home, svc, asks).poll_once()
    (kw,) = svc.list_calls
    assert kw["userId"] == "me" and kw["q"] == QUERY
    assert kw["fields"] == "messages/id"
    for g in svc.get_calls:                                   # the watcher never needs get(); if it ever does,
        assert g.get("format") in ("minimal", "metadata") and not g.get("metadataHeaders")


def test_api_error_logs_error_class_only(modes, home, asks, caplog):
    svc = FakeService(["m1"])
    svc.error = ApiError("quota for Subject: SECRET SUBJECT LINE")
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        assert watcher(modes, home, svc, asks).poll_once() == 0
    (rec,) = [r for r in caplog.records if getattr(r, "jarvis_event", None) == "ofw_watch_error"]
    assert rec.jarvis_fields == {"error_class": "ApiError"} and rec.levelno == logging.ERROR
    assert "SECRET" not in caplog.text and not rec.exc_info
    assert not asks


def test_failed_check_is_an_error_but_ids_are_seen(modes, home, asks, caplog):
    asks.fn.ok = False
    svc = FakeService(["m1"])
    w = watcher(modes, home, svc, asks)
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        w.poll_once()
    errs = [r for r in caplog.records if getattr(r, "jarvis_event", None) == "ofw_watch_error"]
    assert errs[0].jarvis_fields == {"error_class": "OfwCheckFailed"}
    assert w.poll_once() == 0 and len(asks) == 1                 # no retry loop into the model


def test_missing_token_disables_without_raising(modes, home, asks, caplog):
    w = Watcher(modes["personal"], CFG, ask=asks.fn)
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        w.run()                                              # returns instead of polling
    events = [getattr(r, "jarvis_event", None) for r in caplog.records]
    assert events.count("ofw_watch_disabled") == 1 and not asks


def test_corrupt_seen_file_is_reset(modes, home, asks):
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "ofw_watch_seen.json").write_text("{not json")
    assert watcher(modes, home, FakeService(["m1"]), asks).poll_once() == 1


def test_old_seen_ids_are_pruned(modes, home, asks):
    svc = FakeService(["old"])
    w = Watcher(modes["personal"], CFG, ask=asks.fn, service_factory=lambda: svc, clock=lambda: 0.0)
    w.poll_once()
    svc.ids = ["new"]
    later = Watcher(modes["personal"], CFG, ask=asks.fn, service_factory=lambda: svc,
                    clock=lambda: gmail_watch.SEEN_RETENTION_SECONDS + 1.0)
    later.poll_once()
    assert set(json.loads((home / ".jarvis" / "ofw_watch_seen.json").read_text())) == {"new"}


def test_interval_and_defaults(modes, home):
    w = Watcher(modes["personal"], {})
    assert w.interval == 5 * 60 and w.query == QUERY


# ---- --auth ----

class FakeCreds:
    def to_json(self):
        return '{"refresh_token": "r"}'


class FakeFlow:
    seen: dict = {}

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        cls.seen.update(path=path, scopes=scopes, mode=stat.S_IMODE(os.stat(path).st_mode),
                        content=open(path).read())
        return cls()

    def run_local_server(self, **kw):
        FakeFlow.seen["kw"] = kw
        return FakeCreds()


def test_authorize_flow(home, capsys):
    token = home / ".jarvis" / "gmail_watch_token.json"
    email = gmail_watch.authorize('{"installed": {}}', token, flow_cls=FakeFlow,
                                  build_service=lambda creds: FakeService())
    s = FakeFlow.seen
    assert s["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert s["mode"] == 0o600 and s["content"] == '{"installed": {}}' and not os.path.exists(s["path"])
    assert s["kw"]["port"] == 8766 and s["kw"]["open_browser"] is False and s["kw"]["bind_addr"] == "127.0.0.1"
    assert json.loads(token.read_text()) == {"refresh_token": "r"}
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert email == "owner@example.test"


def test_authorize_removes_secrets_on_failure(home):
    class Boom(FakeFlow):
        def run_local_server(self, **kw):
            raise RuntimeError("user closed the browser")
    with pytest.raises(RuntimeError):
        gmail_watch.authorize("{}", home / ".jarvis" / "t.json", flow_cls=Boom, build_service=lambda c: None)
    assert not os.path.exists(Boom.seen["path"])


def test_auth_cli_needs_client_secrets(home, monkeypatch):
    monkeypatch.setattr(gmail_watch, "_client_secrets", lambda profile: None)
    with pytest.raises(SystemExit, match="GMAIL_WATCH_CLIENT_SECRETS"):
        gmail_watch.main(["--auth"])


def test_auth_cli_prints_account(home, monkeypatch, capsys):
    monkeypatch.setattr(gmail_watch, "_client_secrets", lambda profile: "{}")
    monkeypatch.setattr(gmail_watch, "authorize", lambda secrets, path, **kw: "owner@example.test")
    assert gmail_watch.main(["--auth"]) == 0
    assert "owner@example.test" in capsys.readouterr().out


# ---- the loop survives anything a poll throws (gate 3b M2) ----

def _run_two_polls(w, caplog):
    """Run the real loop until it has polled twice, then stop it."""
    polls = []
    real = w.poll_once

    def counted():
        polls.append(1)
        if len(polls) >= 2:
            w.stop()
        return real()
    w.poll_once = counted
    w.interval = 0.001
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        w.run()
    return polls


def _errors(caplog):
    return [r.jarvis_fields for r in caplog.records if getattr(r, "jarvis_event", None) == "ofw_watch_error"]


def test_non_numeric_seen_value_logs_and_loop_continues(modes, home, asks, caplog):
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "gmail_watch_token.json").write_text("{}")
    (home / ".jarvis" / "ofw_watch_seen.json").write_text('{"m1": "not-a-number"}')
    w = watcher(modes, home, FakeService(["m2"]), asks)
    assert len(_run_two_polls(w, caplog)) == 2
    assert {"error_class": "ValueError"} in _errors(caplog)


def test_unwritable_state_dir_logs_and_loop_continues(modes, home, asks, caplog):
    d = home / ".jarvis"
    d.mkdir()
    (d / "gmail_watch_token.json").write_text("{}")
    os.chmod(d, 0o500)
    try:
        w = watcher(modes, home, FakeService(["m1"]), asks)
        assert len(_run_two_polls(w, caplog)) == 2
    finally:
        os.chmod(d, 0o700)
    assert any(e["error_class"] in ("PermissionError", "OSError") for e in _errors(caplog))
