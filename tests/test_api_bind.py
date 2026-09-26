"""Brief 9.3 / AD5: the API refuses to start without a private Tailscale IP and never binds 0.0.0.0."""
import subprocess
import pytest
from fastapi.testclient import TestClient
from jarvis import api, bind
from jarvis.bind import BindSettings, resolve_bind
from jarvis.events import EventBus
from jarvis.handler import make_handler
from .conftest import TOKEN

TS = {"bind": "tailscale", "ports": {"work": 8781, "personal": 8782}}
LOOP = {"bind": "loopback", "ports": {"work": 8781, "personal": 8782}}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def tailscale(monkeypatch):
    """Set .result to a CompletedProcess or an exception; .calls records each invocation."""
    class Fake:
        result = subprocess.CompletedProcess([], 0, stdout="100.101.102.103\n", stderr="")
        calls: list = []

    def run(cmd, **kw):
        Fake.calls.append((cmd, kw))
        if isinstance(Fake.result, BaseException):
            raise Fake.result
        return Fake.result
    Fake.calls = []
    monkeypatch.setattr(bind.subprocess, "run", run)
    return Fake


def done(stdout="", rc=0):
    return subprocess.CompletedProcess(["tailscale"], rc, stdout=stdout, stderr="")


def test_loopback_profile_never_runs_tailscale(tailscale):
    s = resolve_bind(LOOP, "personal")
    assert s == BindSettings("127.0.0.1", 8782, ("127.0.0.1", "localhost"))
    assert not tailscale.calls


def test_tailscale_success(tailscale):
    s = resolve_bind(TS, "work")
    assert s == BindSettings("100.101.102.103", 8781, ("100.101.102.103", "jarvis", "*.ts.net"))
    cmd, kw = tailscale.calls[0]
    assert cmd == ["tailscale", "ip", "-4"] and kw["timeout"] == 10


@pytest.mark.parametrize("result", [done(rc=1), done(""), done("\n"), FileNotFoundError("tailscale"),
                                    subprocess.TimeoutExpired("tailscale", 10), PermissionError("x")])
def test_tailscale_failure_is_startup_error(tailscale, result):
    tailscale.result = result
    with pytest.raises(SystemExit, match="Tailscale"):
        resolve_bind(TS, "work")


@pytest.mark.parametrize("out", ["0.0.0.0", "8.8.8.8", "127.0.0.1", "not-an-ip", "fd7a:115c:a1e0::1",
                                 "169.254.1.1", "100.128.0.1", "100.63.255.255", "10.1.2.3", "172.16.0.9",
                                 "192.168.4.4"])
def test_non_private_address_refused(tailscale, out):
    tailscale.result = done(out + "\n")
    with pytest.raises(SystemExit):
        resolve_bind(TS, "work")


@pytest.mark.parametrize("out", ["100.64.0.1", "100.101.102.103", "100.127.255.254"])
def test_only_tailscale_cgnat_accepted(tailscale, out):
    tailscale.result = done(out + "\n")
    assert resolve_bind(TS, "work").host == out


def test_first_line_only(tailscale):
    tailscale.result = done("100.64.1.2\n100.64.9.9\n")
    assert resolve_bind(TS, "work").host == "100.64.1.2"


@pytest.mark.parametrize("cfg", [{"bind": "public"}, {"bind": "tailscale", "host": "0.0.0.0"},
                                 {"host": "0.0.0.0"}, {"bind": "loopback", "ports": {"work": 0}},
                                 {"bind": "loopback", "ports": {"work": "8781"}}])
def test_bad_config_refused(tailscale, cfg):
    with pytest.raises(SystemExit):
        resolve_bind(cfg, "work")


def test_default_ports_and_port_override():
    assert resolve_bind({}, "work").port == 8781 and resolve_bind({}, "personal").port == 8782
    assert resolve_bind({"port": 9000}, "work").port == 9000


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "8.8.8.8"])
def test_server_config_refuses_unsafe_host(modes, host):
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    with pytest.raises(ValueError):
        api.server_config(app, BindSettings(host, 8781, ("x",)))


def test_serve_binds_tailscale_ip_only(modes, tailscale, monkeypatch):
    seen = {}
    monkeypatch.setattr(api._Server, "run", lambda self, sockets=None: seen.update(config=self.config))
    settings = api.api_settings({"api": TS}, "work")
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus(), bind=settings)
    api.serve(app, settings)
    assert seen["config"].host == "100.101.102.103" and seen["config"].port == 8781
    assert seen["config"].log_config is None                  # uvicorn logs go through logsetup


@pytest.mark.parametrize("base,ok", [("http://jarvis:8781", True), ("http://jarvis.tail1234.ts.net:8781", True),
                                     ("http://100.101.102.103:8781", True), ("http://localhost:8781", False),
                                     ("http://evil.example", False)])
def test_trusted_hosts_follow_bind(modes, tailscale, base, ok):
    settings = resolve_bind(TS, "work")
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus(), bind=settings)
    with TestClient(app, base_url=base) as c:
        assert (c.get("/health", headers=AUTH).status_code == 200) is ok
