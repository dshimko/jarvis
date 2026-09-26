"""ops/jarvis@.service (WSL): one unit per mode, the cross-mode secrets check before every start."""
from pathlib import Path

UNIT = (Path(__file__).resolve().parent.parent / "ops" / "jarvis@.service").read_text(encoding="utf-8")


def lines(key):
    return [line.split("=", 1)[1] for line in UNIT.splitlines() if line.startswith(key + "=")]


def test_per_mode_exec_and_profile():
    assert lines("ExecStart") == ["__JARVIS_DIR__/.venv/bin/python -m jarvis.main --mode %i"]
    assert "JARVIS_DEPLOYMENT=local" in lines("Environment")
    assert lines("WorkingDirectory") == ["__JARVIS_DIR__"]


def test_secrets_check_runs_before_every_start():
    """A separate process: the daemon itself never opens the other mode's env file."""
    assert lines("ExecStartPre") == [
        "__JARVIS_DIR__/.venv/bin/python -m jarvis.secrets_check env/work.env env/personal.env"]
