"""H1: build.ps1 must build from a top-level script, not jarvis_client/__main__.py directly --
PyInstaller runs the entry script as `__main__` with no parent package, so a relative import like
`from . import api` inside jarvis_client/__main__.py would raise ImportError at startup. This
smoke-tests run_tray.py out-of-process (exactly how PyInstaller/Windows would invoke it, and
exactly the failure mode a direct `--onefile jarvis_client/__main__.py` build would hit)."""
import subprocess
import sys
from pathlib import Path

RUN_TRAY = Path(__file__).resolve().parent.parent / "run_tray.py"


def test_run_tray_selftest_imports_cleanly_as_a_top_level_script():
    result = subprocess.run([sys.executable, str(RUN_TRAY), "--selftest"],
                             capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr


def test_running_main_py_directly_would_have_failed_the_same_way(tmp_path):
    """Documents H1's failure mode: running jarvis_client/__main__.py as a top-level script (as a
    naive PyInstaller build would) raises ImportError on its relative imports."""
    windows_client_dir = RUN_TRAY.parent
    main_py = windows_client_dir / "jarvis_client" / "__main__.py"

    result = subprocess.run([sys.executable, str(main_py), "--selftest-would-not-help"],
                            cwd=str(windows_client_dir), capture_output=True, text=True, timeout=30)

    assert result.returncode != 0
    assert "ImportError" in result.stderr or "attempted relative import" in result.stderr
