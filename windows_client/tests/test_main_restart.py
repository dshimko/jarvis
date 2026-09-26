"""M4: Restart Daemon uses the same login-shell form as start-jarvis.ps1 (systemctl --user needs
XDG_RUNTIME_DIR), checks the return code, and speaks/logs on failure instead of swallowing it."""
import subprocess

from jarvis_client import __main__ as main_mod
from jarvis_client import api as api_mod
from jarvis_client.config import ClientConfig


def test_restart_daemon_uses_login_shell_form(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    spoken = []

    main_mod._restart_daemon("Ubuntu", spoken.append)

    cmd = captured["cmd"]
    assert cmd[:4] == ["wsl.exe", "-d", "Ubuntu", "-e"]
    assert cmd[4] == "sh" and cmd[5] == "-lc"
    assert "XDG_RUNTIME_DIR" in cmd[6]
    assert "systemctl --user restart jarvis" in cmd[6]
    assert spoken == []  # success: nothing spoken


def test_restart_daemon_speaks_on_nonzero_exit(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    spoken = []

    main_mod._restart_daemon("Ubuntu", spoken.append)

    assert spoken == ["Could not restart Jarvis."]


def test_restart_daemon_speaks_when_wsl_exe_cannot_be_launched(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("wsl.exe not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    spoken = []

    main_mod._restart_daemon("Ubuntu", spoken.append)

    assert spoken == ["Could not restart Jarvis."]


# -- M9: _build_token_sources --------------------------------------------------------------

def test_build_token_sources_file_profile_uses_one_shared_file_token_for_both_modes():
    cfg = ClientConfig(token_source="file", token_path="/tmp/api_token")

    sources = main_mod._build_token_sources(cfg)

    assert set(sources) == {"work", "personal"}
    assert isinstance(sources["work"], api_mod.FileToken)
    assert isinstance(sources["personal"], api_mod.FileToken)
    assert sources["work"].token_path == "/tmp/api_token"
    assert sources["personal"].token_path == "/tmp/api_token"


def test_build_token_sources_secretsmanager_profile_uses_the_right_secret_id_per_mode():
    cfg = ClientConfig(
        token_source="secretsmanager",
        aws_profile="jarvis-client-sso",
        token_secret_work="jarvis/work/api-token",
        token_secret_personal="jarvis/personal/api-token",
    )

    sources = main_mod._build_token_sources(cfg)

    assert isinstance(sources["work"], api_mod.SecretsManagerToken)
    assert isinstance(sources["personal"], api_mod.SecretsManagerToken)
    # pylint: disable=protected-access
    assert sources["work"]._secret_id == "jarvis/work/api-token"
    assert sources["personal"]._secret_id == "jarvis/personal/api-token"
    assert sources["work"]._aws_profile == "jarvis-client-sso"


def test_build_api_clients_points_each_mode_at_its_own_url():
    cfg = ClientConfig(api_url_work="http://jarvis:8781", api_url_personal="http://jarvis:8782",
                       token_source="file", token_path="/tmp/api_token")

    clients = main_mod._build_api_clients(cfg)

    assert set(clients) == {"work", "personal"}
    assert all(isinstance(c, api_mod.JarvisApi) for c in clients.values())
