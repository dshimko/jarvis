"""Puts windows_client on sys.path so tests can `import jarvis_client...` regardless of how
pytest was invoked (the repo-root pytest.ini's testpaths does not include windows_client)."""
import sys
from pathlib import Path

WINDOWS_CLIENT_DIR = Path(__file__).resolve().parent.parent
if str(WINDOWS_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(WINDOWS_CLIENT_DIR))
