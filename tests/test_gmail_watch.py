"""AD10/AD39: the OFW Gmail watcher reads message ids and internalDate only, dedups locally, asks one
`/ofw-notify <since>` per poll, reports a newly opened OFW breaker once, and logs metadata only."""
import json, logging, os, stat
import pytest
from jarvis import brain, gmail_watch
from jarvis.gmail_watch import Watcher

QUERY = "from:@ourfamilywizard.com is:unread newer_than:2d"
CFG = {"poll_minutes": 5, "query": QUERY}
M1_DATE = "1790345880000"          # 2026-09-25T14:18:00Z
M2_DATE = "1790346600000"          # 2026-09-25T14:30:00Z
M1_SINCE = "2026-09-25T14:03:00+00:00"
M2_SINCE = "2026-09-25T14:15:00+00:00"
NOW = 1790337600.0                 # 2026-09-25T12:00:00Z
NOW_SINCE = "2026-09-25T11:45:00+00:00"
CLOSED = {"status": "ok", "breaker": "closed"}
OPEN = {"status": "ok", "breaker": "open", "breaker_opened_at": "2026-09-25T13:00:00+00:00",
        "breaker_reason": "OFWChallengeRequired"}
NOTICE = ("OFW login is locked, breaker open: OFWChallengeRequired. Fix the credentials or challenge, "
          "then send `ofw reset`.")


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

        def run():
            if kw.get("id") in self.svc.get_errors:
                raise ApiError("Subject: SECRET SUBJECT LINE")
            date = self.svc.dates.get(kw.get("id"))
            return {"id": kw.get("id"), **({"internalDate": date} if date is not None else {})}
        return FakeRequest(run)


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
        self.dates = {i: M1_DATE for i in ids}
        self.get_errors = set()

    def users(self):
        return FakeUsers(self)


class ApiError(Exception):
    pass


class Calls(list):
    fn = None


class Sent(list):
    def __call__(self, text):
        self.append(text)
        return True


@pytest.fixture
def asks(monkeypatch):
    calls = Calls()

    def ask(mode, prompt, **kw):
        calls.append((mode.name, prompt))
        return brain.Answer("drafted", ask.ok)
    ask.ok = True
    calls.fn = ask
    return calls


def watcher(modes, home, svc, asks, status=lambda mode: CLOSED, notify=None, clock=lambda: NOW):
    return Watcher(modes["personal"], CFG, ask=asks.fn, service_factory=lambda: svc, clock=clock,
                   status=status, notify=notify)


def _events(caplog, name):
    return [r.jarvis_fields for r in caplog.records if getattr(r, "jarvis_event", None) == name]


def _errors(caplog):
    return _events(caplog, "ofw_watch_error")


def test_new_ids_trigger_exactly_one_notify(modes, home, asks):
    svc = FakeService(["m1", "m2"])
    svc.dates["m2"] = M2_DATE
    w = watcher(modes, home, svc, asks)
    assert w.poll_once() == 2
    assert asks == [("personal", f"/ofw-notify {M1_SINCE}")]
    seen = home / ".jarvis" / "ofw_watch_seen.json"
    assert set(json.loads(seen.read_text())) == {"m1", "m2"}
    assert stat.S_IMODE(seen.stat().st_mode) == 0o600


def test_seen_ids_do_not_trigger_and_survive_restart(modes, home, asks):
    svc = FakeService(["m1"])
    watcher(modes, home, svc, asks).poll_once()
    w2 = watcher(modes, home, svc, asks)
    assert w2.poll_once() == 0 and len(asks) == 1
    svc.ids.append("m3")
    svc.dates["m3"] = M2_DATE
    assert w2.poll_once() == 1 and len(asks) == 2
    assert asks[-1] == ("personal", f"/ofw-notify {M2_SINCE}")


def test_no_ids_no_check(modes, home, asks):
    assert watcher(modes, home, FakeService([]), asks).poll_once() == 0
    assert not asks


def test_bodies_never_requested(modes, home, asks):
    svc = FakeService(["m1"])
    watcher(modes, home, svc, asks).poll_once()
    (kw,) = svc.list_calls
    assert kw["userId"] == "me" and kw["q"] == QUERY
    assert kw["fields"] == "messages/id"
    assert [g["id"] for g in svc.get_calls] == ["m1"]
    for g in svc.get_calls:                                   # AD39: id and internalDate, nothing else
        assert g == {"userId": "me", "id": g["id"], "format": "minimal", "fields": "id,internalDate"}


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
    assert _errors(caplog)[0] == {"error_class": "OfwNotifyFailed"}
    assert w.poll_once() == 0 and len(asks) == 1                 # no retry loop into the model


def test_missing_token_disables_without_raising(modes, home, asks, caplog):
    w = Watcher(modes["personal"], CFG, ask=asks.fn, status=lambda m: pytest.fail("no status without a token"))
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
    watcher(modes, home, svc, asks, clock=lambda: 0.0).poll_once()
    svc.ids = ["new"]
    watcher(modes, home, svc, asks, clock=lambda: gmail_watch.SEEN_RETENTION_SECONDS + 1.0).poll_once()
    assert set(json.loads((home / ".jarvis" / "ofw_watch_seen.json").read_text())) == {"new"}


def test_interval_and_defaults(modes, home):
    w = Watcher(modes["personal"], {})
    assert w.interval == 5 * 60 and w.query == QUERY
    assert gmail_watch.OFW_NOTIFY == "/ofw-notify" and gmail_watch.NOTIFY_LOOKBACK_MINUTES == 15
    assert not hasattr(gmail_watch, "OFW_CHECK")


# ---- AD39: since, one ask per poll ----

def test_since_is_earliest_new_internal_date_minus_15_minutes(modes, home, asks):
    svc = FakeService(["m2", "m1"])
    svc.dates.update(m1=M1_DATE, m2=M2_DATE)
    watcher(modes, home, svc, asks).poll_once()
    assert asks == [("personal", "/ofw-notify 2026-09-25T14:03:00+00:00")]


def test_seen_ids_are_not_fetched_again(modes, home, asks):
    svc = FakeService(["m1"])
    w = watcher(modes, home, svc, asks)
    w.poll_once()
    svc.ids.insert(0, "m2")
    svc.dates["m2"] = M2_DATE
    w.poll_once()
    assert [g["id"] for g in svc.get_calls] == ["m1", "m2"]
    assert asks[-1] == ("personal", f"/ofw-notify {M2_SINCE}")


def test_one_get_failure_still_one_notify_from_the_others(modes, home, asks, caplog):
    svc = FakeService(["m1", "m2", "m3"])
    svc.dates.update(m2=M2_DATE, m3=M2_DATE)
    svc.get_errors = {"m1"}
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        assert watcher(modes, home, svc, asks).poll_once() == 3
    assert asks == [("personal", f"/ofw-notify {M2_SINCE}")]
    assert {"error_class": "ApiError"} in _errors(caplog) and "SECRET" not in caplog.text


@pytest.mark.parametrize("dates", [{}, {"m1": "not-a-number"}, {"m1": "-5"}, {"m1": "1e400"}])
def test_no_internal_date_uses_now_minus_15(modes, home, asks, dates):
    svc = FakeService(["m1"])
    svc.dates = dict(dates)
    watcher(modes, home, svc, asks).poll_once()
    assert asks == [("personal", f"/ofw-notify {NOW_SINCE}")]


def test_all_gets_fail_uses_now_minus_15(modes, home, asks):
    svc = FakeService(["m1", "m2"])
    svc.get_errors = {"m1", "m2"}
    watcher(modes, home, svc, asks).poll_once()
    assert asks == [("personal", f"/ofw-notify {NOW_SINCE}")]


def test_ask_exception_is_logged(modes, home, asks, caplog):
    def boom(mode, prompt):
        raise RuntimeError("model failed with some text")
    w = Watcher(modes["personal"], CFG, ask=boom, service_factory=lambda: FakeService(["m1"]), clock=lambda: NOW,
                status=lambda m: CLOSED)
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        assert w.poll_once() == 1
    assert {"error_class": "RuntimeError"} in _errors(caplog) and "some text" not in caplog.text


# ---- AD39: breaker notice ----

def test_breaker_notice_once_per_opened_at_across_polls_and_restarts(modes, home, asks):
    sent, state = Sent(), {"s": OPEN}
    svc = FakeService(["m1"])
    w = watcher(modes, home, svc, asks, status=lambda m: state["s"], notify=sent)
    w.poll_once()
    svc.ids.append("m2")
    w.poll_once()
    assert sent == [NOTICE]
    svc.ids.append("m3")
    watcher(modes, home, svc, asks, status=lambda m: state["s"], notify=sent).poll_once()   # restart
    assert sent == [NOTICE]
    state["s"] = {**OPEN, "breaker_opened_at": "2026-09-25T15:00:00+00:00"}
    svc.ids.append("m4")
    w.poll_once()
    assert sent == [NOTICE, NOTICE]
    notified = home / ".jarvis" / "ofw_breaker_notified.json"
    assert stat.S_IMODE(notified.stat().st_mode) == 0o600
    assert len(asks) == 4


def test_status_checked_after_failed_notify_too(modes, home, asks):
    asks.fn.ok = False
    sent = Sent()
    watcher(modes, home, FakeService(["m1"]), asks, status=lambda m: OPEN, notify=sent).poll_once()
    assert sent == [NOTICE]


def test_status_not_checked_without_new_ids(modes, home, asks):
    calls = []
    watcher(modes, home, FakeService([]), asks, status=lambda m: calls.append(m) or OPEN).poll_once()
    assert not calls


@pytest.mark.parametrize("st", [CLOSED, {"status": "ok", "breaker": "open"}, {"status": "error: ConnectError"},
                                {"status": "ok"}, "not a dict"])
def test_no_notice_when_closed_or_undated(modes, home, asks, st):
    sent = Sent()
    watcher(modes, home, FakeService(["m1"]), asks, status=lambda m: st, notify=sent).poll_once()
    assert not sent


def test_unknown_reason_is_fixed_text(modes, home, asks):
    sent = Sent()
    st = {k: v for k, v in OPEN.items() if k != "breaker_reason"}
    watcher(modes, home, FakeService(["m1"]), asks, status=lambda m: st, notify=sent).poll_once()
    assert sent == [NOTICE.replace("OFWChallengeRequired", "unknown")]


def test_status_exception_logs_and_does_not_raise(modes, home, asks, caplog):
    def boom(mode):
        raise ApiError("status failed with Subject: SECRET")
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        assert watcher(modes, home, FakeService(["m1"]), asks, status=boom).poll_once() == 1
    assert _events(caplog, "ofw_status_error") == [{"error_class": "ApiError"}]
    assert "SECRET" not in caplog.text


def test_status_error_word_logs_error_class(modes, home, asks, caplog):
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        watcher(modes, home, FakeService(["m1"]), asks, status=lambda m: {"status": "error: ConnectError"}).poll_once()
    assert _events(caplog, "ofw_status_error") == [{"error_class": "ConnectError"}]


def test_failed_push_is_retried_on_the_next_notify(modes, home, asks):
    attempts = []

    def flaky(text):
        attempts.append(text)
        return len(attempts) > 1
    svc = FakeService(["m1"])
    w = watcher(modes, home, svc, asks, status=lambda m: OPEN, notify=flaky)
    for new in ("m2", "m3"):
        w.poll_once()
        svc.ids.append(new)
    w.poll_once()
    assert attempts == [NOTICE, NOTICE]


@pytest.mark.parametrize("gmail_down", [False, True])
def test_failed_push_is_retried_on_the_next_poll_without_new_ids(modes, home, asks, gmail_down):
    attempts, status_calls = [], []

    def flaky(text):
        attempts.append(text)
        return len(attempts) > 1

    def status(mode):
        status_calls.append(1)
        return OPEN
    svc = FakeService(["m1"])
    w = watcher(modes, home, svc, asks, status=status, notify=flaky)
    w.poll_once()                                            # new id: notify, push fails, not recorded
    assert attempts == [NOTICE] and w._breaker.pending
    if gmail_down:
        svc.error = ApiError("down")
    assert w.poll_once() == 0                                # no new ids: the breaker check still retries
    assert attempts == [NOTICE, NOTICE] and not w._breaker.pending
    notified = json.loads((home / ".jarvis" / "ofw_breaker_notified.json").read_text())
    assert notified == {"breaker_opened_at": OPEN["breaker_opened_at"]}
    w.poll_once()                                            # nothing pending: no status call, no push
    assert len(status_calls) == 2 and len(attempts) == 2 and len(asks) == 1


def test_idle_polls_do_not_check_the_breaker(modes, home, asks):
    status_calls = []
    w = watcher(modes, home, FakeService(["m1"]), asks, status=lambda m: status_calls.append(1) or OPEN,
                notify=Sent())
    w.poll_once()
    w.poll_once()
    assert len(status_calls) == 1 and not w._breaker.pending


def test_push_exception_logged_not_raised(modes, home, asks, caplog):
    def boom(text):
        raise RuntimeError("telegram down")
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        assert watcher(modes, home, FakeService(["m1"]), asks, status=lambda m: OPEN, notify=boom).poll_once() == 1
    assert {"error_class": "RuntimeError"} in _errors(caplog)


def test_no_notify_logs_breaker_once(modes, home, asks, caplog):
    svc = FakeService(["m1"])
    w = watcher(modes, home, svc, asks, status=lambda m: OPEN, notify=None)
    with caplog.at_level(logging.INFO, logger="jarvis.gmail_watch"):
        w.poll_once()
        svc.ids.append("m2")
        w.poll_once()
    assert _events(caplog, "ofw_breaker_open") == [{"error_class": "OFWChallengeRequired"}]


def test_corrupt_notified_file_is_reset(modes, home, asks):
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "ofw_breaker_notified.json").write_text("[not json")
    sent = Sent()
    watcher(modes, home, FakeService(["m1"]), asks, status=lambda m: OPEN, notify=sent).poll_once()
    assert sent == [NOTICE]


def test_default_status_is_ofw_control(modes, home, asks, monkeypatch):
    from jarvis import ofw_control
    seen = []
    monkeypatch.setattr(ofw_control, "status", lambda mode: seen.append(mode.name) or CLOSED)
    Watcher(modes["personal"], CFG, ask=asks.fn, service_factory=lambda: FakeService(["m1"])).poll_once()
    assert seen == ["personal"]


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
