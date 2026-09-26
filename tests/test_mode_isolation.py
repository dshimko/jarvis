"""Brief 10.3: /utterance in work mode never reaches personal secrets, vault, or MCP config (D6, D14, D15, D19)."""
import json, os, subprocess
import pytest
from fastapi.testclient import TestClient
from jarvis import api, brain
from jarvis.events import EventBus
from jarvis.handler import make_handler
from .conftest import PERSONAL_SECRETS, TOKEN, WORK_SECRETS

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def runs(monkeypatch):
    calls = []

    def fake_run(cmd, cwd=None, env=None, **kw):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": "done"}), stderr="")
    monkeypatch.setattr(brain.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def client(modes):
    app = api.create_app(modes, make_handler(modes)[0], TOKEN, EventBus())
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def utter(client, text, mode, **kw):
    r = client.post("/utterance", json={"text": text, "mode": mode, **kw}, headers=AUTH)
    assert r.status_code == 200
    return r.json()


def test_work_utterance_env_and_mcp(client, modes, runs, jev_scores):
    assert utter(client, "summarize my slack", "work") == {"reply": "done", "mode_used": "work", "needs_mode": False,
                                                     "suggested_mode": None}
    call = runs[0]
    env_values = set(call["env"].values())
    assert not env_values & set(PERSONAL_SECRETS)
    assert set(WORK_SECRETS) <= env_values
    assert not any("PERSONAL" in k or k.startswith("OFW") for k in call["env"])
    assert call["env"]["JARVIS_MODE"] == "work"
    assert flag(call["cmd"], "--mcp-config") == str(modes["work"].root / "mcp" / "work.mcp.json")
    assert "--strict-mcp-config" in call["cmd"]
    assert call["cwd"] == modes["work"].vault


def test_work_tool_policy_denies_everything_else(modes, runs, jev_scores, home):
    brain.ask(modes["work"], "hi")
    cmd = runs[0]["cmd"]
    allowed, denied = flag(cmd, "--allowedTools").split(","), flag(cmd, "--disallowedTools").split(",")
    wv, pv, root = modes["work"].vault, modes["personal"].vault, modes["work"].root
    assert f"Read(/{wv}/**)" in allowed and f"Edit(/{wv}/**)" in allowed
    for tree in (pv, root, home / ".claude", home / ".jarvis", home / ".ssh"):
        assert f"Read(/{tree}/**)" in denied and f"Edit(/{tree}/**)" in denied and f"Read(/{tree})" in denied
    assert f"Read(/{home / '.claude.json'})" in denied
    assert f"Edit(/{wv / '.claude'}/**)" in denied and f"Edit(/{wv / '.mcp.json'})" in denied
    assert {"Bash", "WebFetch", "mcp__slack__post_message", "mcp__gmail__send_message"} <= set(denied)
    assert not any("mcp__" in t and "post_message" in t for t in allowed)
    assert json.loads(flag(cmd, "--settings")) == {"disableAllHooks": True}


def test_peer_vault_realpath_denied(modes, tmp_path, runs, jev_scores):
    from dataclasses import replace
    link = tmp_path / "personal-link"
    link.symlink_to(modes["personal"].vault)
    work = replace(modes["work"], peers=(link,))
    _, denied = brain.tool_policy(work)
    assert f"Read(/{link}/**)" in denied
    assert f"Read(/{modes['personal'].vault.resolve()}/**)" in denied


def test_localappdata_denied_when_resolvable(modes, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WIN_LOCALAPPDATA", str(tmp_path / "lad"))
    _, denied = brain.tool_policy(modes["work"])
    assert f"Read(/{tmp_path / 'lad' / 'Jarvis'}/**)" in denied


def test_personal_prefix_under_work_hotkey_needs_mode(client, runs, jev_scores):
    r = utter(client, "personal, what did my ex say", "work")
    assert r["needs_mode"] is True and r["mode_used"] is None
    assert not runs


def test_mode_confirmed_needs_matching_hotkey(client, modes, runs, jev_scores):
    assert utter(client, "personal, what did my ex say", "work", mode_confirmed=True)["needs_mode"] is True
    r = utter(client, "personal, what did my ex say", "personal", mode_confirmed=True)
    assert r["mode_used"] == "personal" and not r["needs_mode"]
    assert runs[0]["cwd"] == modes["personal"].vault
    assert flag(runs[0]["cmd"], "--mcp-config").endswith("personal.mcp.json")


def test_jev_can_only_ask(client, runs, jev_scores):
    jev_scores["mode"] = ("personal", 0.99)
    assert utter(client, "what did the school email say", "work")["needs_mode"] is True
    assert not runs
    r = utter(client, "what did the school email say", "work", mode_confirmed=True)
    assert r["mode_used"] == "work"
    assert runs[0]["env"]["JARVIS_MODE"] == "work"


def test_reply_redacted(client, runs, jev_scores, monkeypatch):
    def leak(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: token ofw-personal-secret-4444")
    monkeypatch.setattr(brain.subprocess, "run", leak)
    r = utter(client, "hello", "work")
    assert "ofw-personal-secret-4444" not in r["reply"] and "[redacted]" in r["reply"]


@pytest.mark.parametrize("name,settings", [("settings.json", {"hooks": {"Stop": []}}),
                                           ("settings.local.json", {"apiKeyHelper": "sh -c evil"}),
                                           ("settings.json", {"permissions": {"allow": ["Bash"]}})])
def test_vault_settings_with_hooks_refused(modes, runs, name, settings):
    d = modes["work"].vault / ".claude"
    d.mkdir()
    (d / name).write_text(json.dumps(settings), encoding="utf-8")
    assert brain.ask(modes["work"], "hi").startswith("Refusing to run")
    assert not runs


def test_vault_settings_unparseable_refused(modes, runs):
    d = modes["work"].vault / ".claude"
    d.mkdir()
    (d / "settings.json").write_text("{not json", encoding="utf-8")
    assert brain.ask(modes["work"], "hi").startswith("Refusing to run")
    assert not runs


def test_benign_vault_settings_allowed(modes, runs):
    d = modes["work"].vault / ".claude"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    assert brain.ask(modes["work"], "hi") == "done"


def test_plan_build_scoped(modes, runs, tmp_path):
    from dataclasses import replace
    repo = tmp_path / "code" / "pulse"
    repo.mkdir(parents=True)
    work = replace(modes["work"], repos={"pulse": repo})
    brain.plan_build(work, "pulse", "plan it")
    cmd = runs[0]["cmd"]
    assert flag(cmd, "--mcp-config") == str(work.mcp_config) and "--strict-mcp-config" in cmd
    assert flag(cmd, "--permission-mode") == "plan"
    assert f"Read(/{modes['personal'].vault}/**)" in flag(cmd, "--disallowedTools")
    assert not set(runs[0]["env"].values()) & set(PERSONAL_SECRETS)


def test_timeout_is_reported(modes, monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(brain.subprocess, "run", slow)
    assert brain.ask(modes["work"], "hi") == "Claude Code timed out."


def test_system_trees_denied(modes):
    """H2: /proc (environ, cmdline), /run/user and /dev/shm, both path forms."""
    _, denied = brain.tool_policy(modes["work"])
    for tree in ("/proc", "/run/user", "/dev/shm"):
        for t in ("Read", "Edit"):
            assert f"{t}(/{tree}/**)" in denied and f"{t}(/{tree})" in denied
        real = os.path.realpath(tree)
        assert f"Read(/{real}/**)" in denied


def test_own_vault_obsidian_and_git_write_denied(modes, tmp_path):
    """H3: both path forms of .obsidian and .git are Edit-denied (Edit also covers Write)."""
    from dataclasses import replace
    link = tmp_path / "vault-link"
    link.symlink_to(modes["work"].vault)
    allowed, denied = brain.tool_policy(replace(modes["work"], vault=link))
    real = modes["work"].vault.resolve()
    for d in (".obsidian", ".git", ".claude"):
        assert f"Edit(/{link / d}/**)" in denied and f"Edit(/{real / d}/**)" in denied
    assert f"Edit(/{link}/**)" in allowed


@pytest.mark.parametrize("config", ["[core]\n\tfsmonitor = /tmp/evil\n", "[core]\n\tHooksPath = .hooks\n",
                                    "[CORE]\n\tFSMonitor = true\n"])
def test_git_config_with_exec_hooks_refused(modes, runs, config):
    g = modes["work"].vault / ".git"
    g.mkdir()
    (g / "config").write_text(config, encoding="utf-8")
    assert "Refusing to run" in brain.ask(modes["work"], "hi")
    assert not runs


def test_benign_git_config_allowed(modes, runs):
    g = modes["work"].vault / ".git"
    g.mkdir()
    (g / "config").write_text("[core]\n\tautocrlf = false\n\tfilemode = false\n", encoding="utf-8")
    assert brain.ask(modes["work"], "hi") == "done"


def test_ask_detailed_reports_failure(modes, monkeypatch):
    monkeypatch.setattr(brain.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="bad"))
    assert brain.ask_detailed(modes["work"], "hi") == brain.Answer("Claude Code failed: bad", False)


def test_ask_detailed_redacts_stderr_before_truncating(modes, monkeypatch):
    """A secret spanning the [:300] cut must be fully gone, not leak a prefix (M6)."""
    secret = "SUPERSECRET1234567890"
    stderr = ("x" * 290) + secret          # secret starts before, and runs past, the 300-char cut
    monkeypatch.setattr(brain.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr=stderr))
    brain.set_redactor(lambda t: t.replace(secret, "[redacted]"))
    try:
        out = brain.ask_detailed(modes["work"], "hi")
    finally:
        brain.set_redactor(None)
    assert secret[:8] not in out.text


def test_plan_build_redacts_stderr_before_truncating(modes, monkeypatch, tmp_path):
    """Same fix at the plan_build [:500] cut."""
    from dataclasses import replace
    secret = "SUPERSECRET1234567890"
    stderr = ("x" * 490) + secret          # secret starts before, and runs past, the 500-char cut
    monkeypatch.setattr(brain.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr=stderr))
    brain.set_redactor(lambda t: t.replace(secret, "[redacted]"))
    repo = tmp_path / "code" / "pulse"
    repo.mkdir(parents=True)
    work = replace(modes["work"], repos={"pulse": repo})
    try:
        out = brain.plan_build(work, "pulse", "plan it")
    finally:
        brain.set_redactor(None)
    assert secret[:8] not in out
