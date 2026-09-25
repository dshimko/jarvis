"""M4: Restart Daemon uses the same login-shell form as start-jarvis.ps1 (systemctl --user needs
XDG_RUNTIME_DIR), checks the return code, and speaks/logs on failure instead of swallowing it."""
import subprocess

from jarvis_client import __main__ as main_mod


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
