"""jev._haiku_fallback (C9): minimal env, no MCP servers, no hooks, neutral cwd, fails closed."""
import subprocess
import pytest
from jarvis import brain, jev, outbox
from jarvis.modes import PASSTHROUGH, ROOT
from .conftest import approve_ok, make_item

QS = {"leak": jev.probability("does this leak?")}


class FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


GOOD_STDOUT = '{"result": "{\\"leak\\": {\\"value\\": 0.1, \\"confidence\\": 0.9}}"}'


@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return FakeCompleted(stdout=GOOD_STDOUT)
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_strict_mcp_config_and_no_mcp_servers(home, fake_run):
    jev._haiku_fallback("state", QS)
    cmd = fake_run[0][0]
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" not in cmd


def test_hooks_disabled_like_brain(home, fake_run):
    jev._haiku_fallback("state", QS)
    cmd = fake_run[0][0]
    assert jev.NO_HOOKS == brain.NO_HOOKS
    assert cmd[cmd.index("--settings") + 1] == jev.NO_HOOKS


def test_env_has_no_mode_secrets(home, fake_run, monkeypatch):
    monkeypatch.setenv("SLACK_WORK_TOKEN", "xoxp-work-secret-1111")
    monkeypatch.setenv("OFW_MCP_TOKEN", "ofw-personal-secret-4444")
    jev._haiku_fallback("state", QS)
    env = fake_run[0][1]["env"]
    assert set(env) <= PASSTHROUGH
    assert "SLACK_WORK_TOKEN" not in env and "OFW_MCP_TOKEN" not in env


def test_cwd_is_not_repo_or_vault(home, fake_run, tmp_path):
    jev._haiku_fallback("state", QS)
    cwd = fake_run[0][1]["cwd"]
    assert cwd != ROOT
    assert not str(cwd).endswith("Vaults/Jarvis-Work") and not str(cwd).endswith("Vaults/Jarvis-Personal")
    assert str(cwd).endswith(".jarvis")


def test_timeout_raises(home, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=60)
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError):
        jev._haiku_fallback("state", QS)


def test_start_failure_raises(home, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no claude on PATH")))
    with pytest.raises(RuntimeError):
        jev._haiku_fallback("state", QS)


def test_nonzero_exit_raises(home, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(returncode=1, stdout="{}"))
    with pytest.raises(RuntimeError):
        jev._haiku_fallback("state", QS)


def test_malformed_json_raises(home, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(stdout="not json"))
    with pytest.raises(RuntimeError):
        jev._haiku_fallback("state", QS)


def test_malformed_result_field_raises(home, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(stdout='{"result": "still not json"}'))
    with pytest.raises(RuntimeError):
        jev._haiku_fallback("state", QS)


# ---- integration: outbox.check_gates fails closed when Jev is fully unavailable ----

def test_check_gates_fails_closed_when_jev_raises(modes, mcp_calls, monkeypatch):
    """_check_jev does not catch: a decide() failure must block the send, never approve it (C9 + D-gates)."""
    m = modes["work"]
    make_item(m, "no-jev")
    approve_ok(m, "no-jev")

    def boom(env, state, questions):
        raise RuntimeError("jev api down and haiku fallback failed")
    monkeypatch.setattr(jev, "decide", boom)

    with pytest.raises(RuntimeError):
        outbox.execute(m, "no-jev")
    assert not mcp_calls
    meta, _ = outbox.read_item(m.vault / "outbox" / "no-jev.md")
    assert meta["status"] == "approved"          # never flipped to sending/sent
