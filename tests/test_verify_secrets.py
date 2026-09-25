"""Brief 10.6 / C8: no env value on the Windows side. Output has key names and paths, never values."""
import os
import pytest
from jarvis import verify_secrets

SECRET = "xoxp-verify-secret-9999"


@pytest.fixture
def repo(root, home):
    (root / "env" / "work.env").write_text(f"SLACK_WORK_TOKEN={SECRET}\nSHORT=abc\n", encoding="utf-8")
    (root / "env" / "personal.env").write_text("OFW_MCP_TOKEN=ofw-verify-secret-8888\n", encoding="utf-8")
    (root / "windows_client").mkdir()
    (root / "windows_client" / "client.yaml").write_text("api_url: http://localhost:8765\n", encoding="utf-8")
    return root


def test_clean_exits_zero(repo, capsys):
    assert verify_secrets.main([], root=repo) == 0
    assert "clean" in capsys.readouterr().out


def test_found_prints_key_not_value(repo, capsys):
    leak = repo / "windows_client" / "notes.txt"
    leak.write_text(f"token={SECRET}", encoding="utf-8")
    assert verify_secrets.main([], root=repo) == 1
    out = capsys.readouterr().out
    assert "SLACK_WORK_TOKEN" in out and str(leak) in out
    assert SECRET not in out


def test_utf16_detected(repo, capsys):
    (repo / "windows_client" / "win.log").write_bytes("x ofw-verify-secret-8888 y".encode("utf-16-le"))
    assert verify_secrets.main([], root=repo) == 1
    assert "OFW_MCP_TOKEN" in capsys.readouterr().out


def test_localappdata_and_extra_dirs_scanned(repo, tmp_path, monkeypatch, capsys):
    lad = tmp_path / "lad"
    (lad / "Jarvis").mkdir(parents=True)
    (lad / "Jarvis" / "api_token").write_text("ab" * 32)
    monkeypatch.setenv("JARVIS_WIN_LOCALAPPDATA", str(lad))
    assert verify_secrets.main([], root=repo) == 0
    (lad / "Jarvis" / "client.log").write_text(SECRET)
    assert verify_secrets.main([], root=repo) == 1
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "f").write_text("ofw-verify-secret-8888")
    os.remove(lad / "Jarvis" / "client.log")
    assert verify_secrets.main(["--extra-dir", str(extra)], root=repo) == 1
    assert "OFW_MCP_TOKEN" in capsys.readouterr().out


def test_short_values_ignored(repo):
    (repo / "windows_client" / "x").write_text("abc")
    assert verify_secrets.main([], root=repo) == 0


def test_no_env_files(root, home, capsys):
    assert verify_secrets.main([], root=root) == 0
    assert "No env values" in capsys.readouterr().out
