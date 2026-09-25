"""modes.load_modes (B1, B7, C2, shared-secret guard) and paths helpers (B5)."""
import os
from pathlib import Path
import pytest
from jarvis import paths
from jarvis.modes import CFG, check_env_file, load_modes, make_redactor


def write_env(root: Path, name: str, lines: dict, mode: int = 0o600) -> Path:
    p = root / "env" / f"{name}.env"
    p.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
    os.chmod(p, mode)
    return p


@pytest.fixture
def envs(root):
    write_env(root, "work", {"SLACK_WORK_TOKEN": "work-token-abcdef12", "JEV_API_KEY": "same-jev-key-123"})
    write_env(root, "personal", {"OFW_MCP_TOKEN": "ofw-token-abcdef34", "JEV_API_KEY": "same-jev-key-123"})
    return root


def test_load_modes_expands_win_home(envs, home, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WIN_HOME", str(tmp_path / "winhome"))
    modes = load_modes(root=envs, cfg=CFG)
    assert modes["work"].vault == tmp_path / "winhome" / "Vaults" / "Jarvis-Work"
    assert modes["personal"].vault == tmp_path / "winhome" / "Vaults" / "Jarvis-Personal"
    assert modes["work"].peers == (modes["personal"].vault,)
    assert modes["work"].mcp_config == envs / "mcp" / "work.mcp.json"
    assert modes["work"].env["SLACK_WORK_TOKEN"] == "work-token-abcdef12"
    assert "OFW_MCP_TOKEN" not in modes["work"].subprocess_env()


def test_win_home_from_hint_file(envs, home, tmp_path):
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "win_home").write_text(f"{tmp_path / 'hinted'}\n")
    assert load_modes(root=envs, cfg=CFG)["work"].vault == tmp_path / "hinted" / "Vaults" / "Jarvis-Work"


def test_env_var_beats_hint(home, tmp_path, monkeypatch):
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "win_home").write_text("/mnt/c/Users/hint")
    monkeypatch.setenv("JARVIS_WIN_HOME", "/mnt/c/Users/env")
    assert paths.windows_home() == Path("/mnt/c/Users/env")


def test_unresolvable_win_home_refuses(envs, home):
    with pytest.raises(SystemExit, match="WIN_HOME"):
        load_modes(root=envs, cfg=CFG)


@pytest.mark.parametrize("perm", [0o640, 0o604, 0o644])
def test_readable_env_file_refused(envs, home, tmp_path, monkeypatch, perm):
    monkeypatch.setenv("JARVIS_WIN_HOME", str(tmp_path))
    os.chmod(envs / "env" / "work.env", perm)
    with pytest.raises(SystemExit, match="group/world"):
        load_modes(root=envs, cfg=CFG)


def test_env_file_on_windows_fs_refused():
    with pytest.raises(SystemExit, match="Windows filesystem"):
        check_env_file(Path("/mnt/c/Users/x/jarvis/env/work.env"))


def test_env_symlink_into_mnt_refused(tmp_path, monkeypatch):
    link = tmp_path / "work.env"
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: Path("/mnt/c/Users/x/work.env"))
    with pytest.raises(SystemExit, match="Windows filesystem"):
        check_env_file(link)


def test_missing_env_file_allowed(root, home, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WIN_HOME", str(tmp_path))
    assert load_modes(root=root, cfg=CFG)["work"].env == {}


def test_shared_secret_refused(root, home, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WIN_HOME", str(tmp_path))
    write_env(root, "work", {"GMAIL_TOKEN": "shared-value-123456"})
    write_env(root, "personal", {"GMAIL_TOKEN": "shared-value-123456"})
    with pytest.raises(SystemExit, match="shared across modes"):
        load_modes(root=root, cfg=CFG)


def test_literal_secret_in_mcp_config_refused(envs, home, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WIN_HOME", str(tmp_path))
    cfg_path = envs / "mcp" / "personal.mcp.json"
    cfg_path.write_text(cfg_path.read_text().replace("${OFW_MCP_TOKEN}", "work-token-abcdef12"))
    with pytest.raises(SystemExit, match="literal secret"):
        load_modes(root=envs, cfg=CFG)


def test_redactor(modes):
    redact = make_redactor(modes)
    assert redact("a xoxp-work-secret-1111 b ofw-personal-secret-4444") == "a [redacted] b [redacted]"
    assert redact(None) is None


# ---- paths ----

def test_to_windows_mnt():
    assert paths.to_windows("/mnt/c/Users/dushan/Vaults/Jarvis-Work/outbox/x.md") == \
        "C:\\Users\\dushan\\Vaults\\Jarvis-Work\\outbox\\x.md"
    assert paths.to_windows("/mnt/d") == "D:\\"


def test_obsidian_uri_encodes():
    assert paths.obsidian_uri(Path("/mnt/c/Users/x/Vaults/Jarvis Work"), "outbox/a b&c.md") == \
        "obsidian://open?vault=Jarvis%20Work&file=outbox%2Fa%20b%26c.md"


def test_localappdata_resolution(home, tmp_path, monkeypatch):
    assert paths.jarvis_localappdata() is None
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "win_localappdata").write_text("/mnt/c/Users/x/AppData/Local\n")
    assert paths.jarvis_localappdata() == Path("/mnt/c/Users/x/AppData/Local/Jarvis")


def test_interop_fallback(home, monkeypatch):
    monkeypatch.setattr(paths, "_interop", lambda var: "/mnt/c/Users/interop" if var == "USERPROFILE" else None)
    assert paths.windows_home() == Path("/mnt/c/Users/interop")
