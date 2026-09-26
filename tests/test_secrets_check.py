"""AD4/AD7: the cross-mode shared-secret check is a pure function reporting key names, never values."""
import os
import pytest
from jarvis import secrets_check
from jarvis.secrets_check import shared_violations

W = {"SLACK_WORK_TOKEN": "work-only-value-1", "GMAIL_MCP_URL": "https://same.example/mcp",
     "JEV_API_KEY": "same-jev-key-123", "EMPTY": ""}
P = {"OFW_MCP_TOKEN": "personal-only-value", "GMAIL_MCP_URL": "https://same.example/mcp",
     "JEV_API_KEY": "same-jev-key-123", "EMPTY": ""}


def test_identical_values_reported_by_name_only():
    out = shared_violations(W, P, frozenset())
    assert out == ["GMAIL_MCP_URL", "JEV_API_KEY"]
    assert not any("same" in k for k in out)


def test_shared_keys_are_exempt():
    assert shared_violations(W, P, frozenset({"GMAIL_MCP_URL", "JEV_API_KEY"})) == []


def test_jev_prefix_exempt_only_when_asked():
    assert shared_violations(W, P, frozenset(), exempt_prefixes=("JEV_",)) == ["GMAIL_MCP_URL"]


def test_different_or_empty_values_are_fine():
    assert shared_violations({"A": "x" * 10, "B": ""}, {"A": "y" * 10, "B": ""}, frozenset()) == []


def test_same_value_under_different_keys_is_reported():
    out = shared_violations({"A": "shared-secret-1"}, {"B": "shared-secret-1"}, frozenset())
    assert out == ["A", "B"]


def write_env(path, lines):
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_cli_violation_exits_1_without_values(tmp_path, capsys):
    w = write_env(tmp_path / "work.env", {"GMAIL_TOKEN": "shared-value-123456", "JEV_API_KEY": "jev-shared-1"})
    p = write_env(tmp_path / "personal.env", {"GMAIL_TOKEN": "shared-value-123456", "JEV_API_KEY": "jev-shared-1"})
    assert secrets_check.main([str(w), str(p)]) == 1
    out = capsys.readouterr().out
    assert "GMAIL_TOKEN" in out and "shared-value-123456" not in out
    assert "JEV_API_KEY" not in out                          # local behavior: one Jev key may serve both


def test_cli_clean_and_missing_files(tmp_path, capsys):
    w = write_env(tmp_path / "work.env", {"A": "value-one-111"})
    assert secrets_check.main([str(w), str(tmp_path / "absent.env")]) == 0
    assert "clean" in capsys.readouterr().out


def test_cli_needs_two_paths():
    with pytest.raises(SystemExit):
        secrets_check.main([])


# ---- literal secrets in the repo MCP configs (gate 3b M4) ----

def mcp_root(tmp_path, work_extra="", personal_extra=""):
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "work.mcp.json").write_text('{"mcpServers": {"slack": {"url": "${SLACK_MCP_URL}"' +
                                                    work_extra + "}}}")
    (tmp_path / "mcp" / "personal.mcp.json").write_text('{"mcpServers": {"ofw": {"url": "${OFW_MCP_URL}"' +
                                                        personal_extra + "}}}")
    return tmp_path


WORK = {"SLACK_WORK_TOKEN": "xoxp-work-secret-1111", "SHORT": "abc"}
PERS = {"OFW_MCP_TOKEN": "ofw-personal-secret-4444"}


def test_mcp_clean():
    from jarvis.secrets_check import mcp_literal_violations
    assert mcp_literal_violations(secrets_check.ROOT, {}, {}) == []


def test_mcp_cross_and_own_literals_reported_by_name(tmp_path):
    from jarvis.secrets_check import mcp_literal_violations
    root = mcp_root(tmp_path, work_extra=', "h": "ofw-personal-secret-4444"',
                    personal_extra=', "h": "Bearer xoxp-work-secret-1111", "o": "ofw-personal-secret-4444"')
    out = mcp_literal_violations(root, WORK, PERS)
    assert out == ["mcp/personal.mcp.json: OFW_MCP_TOKEN", "mcp/personal.mcp.json: SLACK_WORK_TOKEN",
                   "mcp/work.mcp.json: OFW_MCP_TOKEN"]
    assert not any("secret-" in o for o in out)


def test_mcp_short_values_ignored_and_missing_files_ok(tmp_path):
    from jarvis.secrets_check import mcp_literal_violations
    root = mcp_root(tmp_path, work_extra=', "x": "abc"')
    assert mcp_literal_violations(root, WORK, PERS) == []
    assert mcp_literal_violations(tmp_path / "nowhere", WORK, PERS) == []


def test_cli_runs_mcp_check(tmp_path, capsys, monkeypatch):
    root = mcp_root(tmp_path, work_extra=', "h": "ofw-personal-secret-4444"')
    w = write_env(tmp_path / "work.env", WORK)
    p = write_env(tmp_path / "personal.env", PERS)
    monkeypatch.setattr(secrets_check, "ROOT", root)
    assert secrets_check.main([str(w), str(p)]) == 1
    out = capsys.readouterr().out
    assert "mcp/work.mcp.json: OFW_MCP_TOKEN" in out and "ofw-personal-secret-4444" not in out


def test_pure_functions_import_with_stdlib_only(monkeypatch):
    """ops/aws/bin/jarvis-secrets imports this module with the instance's system python3 (no dotenv, no yaml)."""
    import importlib, sys
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.delitem(sys.modules, "jarvis.config", raising=False)
    try:
        mod = importlib.reload(secrets_check)
        assert mod.shared_violations({"A": "same-value-1"}, {"A": "same-value-1"}, frozenset()) == ["A"]
        assert mod.mcp_literal_violations(mod.ROOT, {}, {}) == []
    finally:
        monkeypatch.undo()
        importlib.reload(secrets_check)
