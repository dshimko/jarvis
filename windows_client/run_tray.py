"""PyInstaller entry point (H1).

PyInstaller runs the built exe's entry script as a top-level module named "__main__", which has
no parent package. jarvis_client/__main__.py uses package-relative imports (`from . import api`),
so building directly from it makes those imports raise ImportError at startup -- the hidden tray
process would die silently at every logon. This tiny top-level script sits next to the
jarvis_client/ package instead: run as `python run_tray.py`, its own directory is put on
sys.path, so `import jarvis_client` resolves it as a proper package and its internal relative
imports work normally. build.ps1 builds from this file, not from jarvis_client/__main__.py.

`--selftest` only imports jarvis_client.__main__ (exercising every submodule's own top-level
imports) and exits 0, without opening a mic, a tray icon, or a network connection -- this is what
the test suite runs to catch an import-time regression like H1 without actually running the
client.
"""
from __future__ import annotations

import sys


def main() -> None:
    from jarvis_client.__main__ import main as run_client

    run_client()


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        import jarvis_client.__main__  # noqa: F401 -- import-only smoke test, see docstring

        sys.exit(0)
    main()
