"""The OFW watcher's Google libraries come from requirements.txt; a fresh venv without them fails here."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_google_libraries_import():
    import googleapiclient.discovery  # noqa: F401
    import google.oauth2.credentials  # noqa: F401
    import google_auth_oauthlib.flow  # noqa: F401


def test_google_libraries_are_declared():
    reqs = (REPO / "requirements.txt").read_text(encoding="utf-8").lower()
    for name in ("google-api-python-client>=", "google-auth>=", "google-auth-oauthlib>="):
        assert name in reqs
