"""Brief 10.1: 401 on missing/wrong token, 127.0.0.1 bind only, token file handling (C5-C7)."""
import logging, os, stat
import pytest
from fastapi.testclient import TestClient
from jarvis import api
from jarvis.bind import BindSettings
from jarvis.events import EventBus
from jarvis.handler import make_handler
from .conftest import TOKEN

ENDPOINTS = [("get", "/health", None), ("get", "/outbox?mode=work", None), ("get", "/events", None),
             ("post", "/utterance", {"text": "hi", "mode": "work"}),
             ("post", "/approve", {"mode": "work", "id": "x", "body_sha256": "0" * 64, "readback_sha256": "0" * 64})]


@pytest.fixture
def client(modes):
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_missing_token_401(client, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", [f"Bearer {'cd' * 32}", f"Basic {TOKEN}", TOKEN, "Bearer ", f"Bearer {TOKEN}x"])
@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_wrong_token_401(client, method, path, body, header):
    assert client.request(method, path, json=body, headers={"Authorization": header}).status_code == 401


def test_failed_auth_logged_without_token(client, caplog):
    bad = "ef" * 32
    with caplog.at_level(logging.WARNING, logger="jarvis.api"):
        client.get("/health", headers={"Authorization": f"Bearer {bad}"})
    assert "auth failed" in caplog.text and "/health" in caplog.text
    assert bad not in caplog.text and TOKEN not in caplog.text


def test_health_ok_with_token(client, modes):
    r = client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["modes"]["work"] == {"vault": True, "mcp_config": True, "pending": 0}
    assert body["deployment"] == "local"
    assert str(modes["work"].vault.parent) not in r.text and "TOKEN" not in r.text   # C4: no paths, no env keys


def test_untrusted_host_rejected(modes):
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    with TestClient(app, base_url="http://evil.example") as c:
        assert c.get("/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 400


def test_no_docs_or_cors(client):
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/docs", headers=auth).status_code == 404
    assert client.get("/openapi.json", headers=auth).status_code == 404
    r = client.options("/health", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


# ---- bind address (loopback profile; the tailscale profile is tests/test_api_bind.py) ----

def test_settings_default_loopback():
    assert api.api_settings({}, "work") == BindSettings("127.0.0.1", 8781, ("127.0.0.1", "localhost"))
    assert api.api_settings({"api": {"port": 9000}}, "work").port == 9000


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
def test_non_loopback_host_refused(host):
    with pytest.raises(SystemExit):
        api.api_settings({"api": {"host": host, "port": 8765}}, "work")


@pytest.mark.parametrize("port", [0, 70000, "8765", None])
def test_bad_port_refused(port):
    with pytest.raises(SystemExit):
        api.api_settings({"api": {"port": port}}, "work")


def test_serve_binds_127_0_0_1(modes, monkeypatch):
    seen = {}

    def fake_run(self, sockets=None):
        seen.update(config=self.config, ran=True)
    monkeypatch.setattr(api._Server, "run", fake_run)
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    api.serve(app, api.api_settings({"api": {"bind": "loopback", "port": 8765}}, "work"))
    assert seen["ran"] and seen["config"].host == "127.0.0.1" and seen["config"].port == 8765
    assert seen["config"].timeout_graceful_shutdown == 5
    with pytest.raises(ValueError):
        api.server_config(app, BindSettings("0.0.0.0", 8765, ("localhost",)))


# ---- token ----

def test_token_created_0600_and_reused(home):
    t = api.load_or_create_token()
    p = home / ".jarvis" / "api_token"
    assert len(t) == 64 and int(t, 16) >= 0
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert api.load_or_create_token() == t


def test_token_permissions_tightened(home):
    t = api.load_or_create_token()
    p = home / ".jarvis" / "api_token"
    os.chmod(p, 0o644)
    assert api.load_or_create_token() == t
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_invalid_token_file_refused(home):
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "api_token").write_text("short\n")
    with pytest.raises(SystemExit):
        api.load_or_create_token()


def test_token_copied_to_windows(home, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WIN_LOCALAPPDATA", str(tmp_path / "lad"))
    dest = api.copy_token_to_windows(TOKEN)
    assert dest == tmp_path / "lad" / "Jarvis" / "api_token"
    assert dest.read_text().strip() == TOKEN


@pytest.mark.parametrize("deployment,copied", [("local", True), ("aws", False)])
def test_token_copied_only_under_local(home, tmp_path, monkeypatch, deployment, copied):
    """AD6: on AWS the root jarvis-secrets tool publishes the token; the daemon never copies it anywhere."""
    calls = []
    monkeypatch.setattr(api, "copy_token_to_windows", lambda token: calls.append(token) or tmp_path / "t")
    api.share_token(TOKEN, deployment)
    assert (calls == [TOKEN]) is copied


def test_token_copy_skipped_when_windows_unresolvable(home, caplog):
    with caplog.at_level(logging.WARNING):
        assert api.copy_token_to_windows(TOKEN) is None
    assert "not copied" in caplog.text


def test_create_app_rejects_bad_token(modes):
    with pytest.raises(ValueError):
        api.create_app(modes, make_handler(modes)[0], "short", EventBus())


def test_stop_signal_marks_streams_exiting(modes):
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    server = api._Server(api.server_config(app, api.api_settings({}, "work")), app.state.exiting)
    server.handle_exit(15, None)
    assert app.state.exiting.is_set() and server.should_exit
