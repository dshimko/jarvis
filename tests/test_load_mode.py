"""Brief 9.3 / AD4: the per-mode loader builds one Mode and never opens the other mode's env file."""
import builtins, io, os
from pathlib import Path
import pytest
from jarvis import modes as modes_mod
from jarvis.config import load_config, deep_merge
from jarvis.modes import load_mode, make_redactor

TRIPWIRE = "TRIPWIRE-other-mode-secret-9f8e7d6c"
REPO = modes_mod.ROOT


def write_env(path: Path, lines: dict, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
    os.chmod(path, mode)
    return path


@pytest.fixture
def opened(monkeypatch):
    """Records every path passed to open(), io.open() and os.open()."""
    seen: list[str] = []
    real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

    def rec(real):
        def wrapper(file, *a, **k):
            seen.append(os.fspath(file) if isinstance(file, (str, os.PathLike)) else str(file))
            return real(file, *a, **k)
        return wrapper
    monkeypatch.setattr(builtins, "open", rec(real_open))
    monkeypatch.setattr(io, "open", rec(real_io_open))
    monkeypatch.setattr(os, "open", rec(real_os_open))
    return seen


def aws_cfg(tmp_path: Path) -> dict:
    """The repo's aws profile with the /home/jarvis-{mode} prefix moved under tmp_path."""
    cfg = load_config(REPO, "aws")
    over = {"modes": {n: {"vault": f"{tmp_path}/home/jarvis-{{mode}}/vault",
                          "env_file": f"{tmp_path}/home/jarvis-{{mode}}/.jarvis/env"} for n in cfg["modes"]}}
    return deep_merge(cfg, over)


@pytest.fixture
def aws_homes(tmp_path, root, home):
    cfg = aws_cfg(tmp_path)
    work_env = write_env(tmp_path / "home" / "jarvis-work" / ".jarvis" / "env",
                         {"SLACK_WORK_TOKEN": "work-token-abcdef12"})
    pers_env = write_env(tmp_path / "home" / "jarvis-personal" / ".jarvis" / "env",
                         {"OFW_MCP_TOKEN": TRIPWIRE}, mode=0o000)
    yield cfg, work_env, pers_env
    os.chmod(pers_env, 0o600)


def test_other_env_never_opened_aws(aws_homes, root, tmp_path, opened):
    cfg, work_env, pers_env = aws_homes
    work = load_mode("work", root, cfg)
    assert str(pers_env) not in opened and str(work_env) in opened
    assert work.env == {"SLACK_WORK_TOKEN": "work-token-abcdef12"}
    assert TRIPWIRE not in work.subprocess_env().values()
    assert make_redactor({"work": work})(TRIPWIRE) == TRIPWIRE      # not a needle: it was never loaded
    assert work.vault == tmp_path / "home" / "jarvis-work" / "vault"
    assert work.peers == (tmp_path / "home" / "jarvis-personal" / "vault",)
    assert work.obsidian_vault_name == "Jarvis-Work"


def test_other_env_never_opened_local(root, home, tmp_path, monkeypatch, opened):
    monkeypatch.setenv("JARVIS_WIN_HOME", str(tmp_path / "win"))
    write_env(root / "env" / "personal.env", {"SLACK_WORK_TOKEN": "p-token-123456"})
    work_env = write_env(root / "env" / "work.env", {"OFW_MCP_TOKEN": TRIPWIRE}, mode=0o000)
    opened.clear()                                   # the writes above are the test's own
    try:
        personal = load_mode("personal", root, load_config(REPO, "local"))
    finally:
        os.chmod(work_env, 0o600)
    assert str(work_env) not in opened
    assert personal.peers == (tmp_path / "win" / "Vaults" / "Jarvis-Work",)
    assert TRIPWIRE not in personal.subprocess_env().values()


def test_load_modes_is_gone():
    assert not hasattr(modes_mod, "load_modes")


def test_unknown_mode_refused(root, home):
    with pytest.raises(SystemExit, match="unknown mode"):
        load_mode("guest", root, load_config(REPO, "aws"))


def test_own_literal_secret_in_mcp_config_refused(aws_homes, root):
    cfg, _, _ = aws_homes
    p = root / "mcp" / "work.mcp.json"
    p.write_text(p.read_text().replace("${SLACK_WORK_TOKEN}", "work-token-abcdef12"))
    with pytest.raises(SystemExit, match="literal secret"):
        load_mode("work", root, cfg)


def test_profile_argument_loads_config(root, home, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WIN_HOME", str(tmp_path / "win"))
    for name in ("config.yaml", "config.local.yaml", "config.aws.yaml"):
        (root / name).write_text((REPO / name).read_text())
    work = load_mode("work", root, profile="local")
    assert work.vault == tmp_path / "win" / "Vaults" / "Jarvis-Work"
    assert work.obsidian_vault_name is None
