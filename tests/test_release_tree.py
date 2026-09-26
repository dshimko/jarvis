"""H1 (Gate re-verification): builds the exact tree scripts/release.sh archives into a release
tarball (`git archive HEAD` over the same paths, parsed straight out of that script so this test
cannot silently drift out of sync with it) and runs `pytest --collect-only` inside it under a
clean environment -- exactly what jarvis-deploy.sh's build_and_test step does on the box, minus
the venv/hash-pinned install (collection alone is enough to catch "ARCHIVE_PATHS is missing a
file some test needs", which is what broke on-box `pytest -q` for every release). Skipped
cleanly outside a git checkout.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release.sh"


def _in_git_checkout() -> bool:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    ).returncode == 0


pytestmark = pytest.mark.skipif(not _in_git_checkout(), reason="not a git checkout")


def _archive_paths() -> list[str]:
    """The same space-separated path list release.sh's own ARCHIVE_PATHS variable holds, parsed
    from the script's source (not hand-copied here) so a future edit to one is guaranteed to be
    reflected in the other."""
    text = RELEASE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'^ARCHIVE_PATHS="([^"]+)"', text, re.MULTILINE)
    assert match, "could not find ARCHIVE_PATHS in scripts/release.sh"
    return match.group(1).split()


def _config_files() -> list[str]:
    """release.sh's own `git ls-files -- 'config*.yaml'` step, reproduced exactly."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", "config*.yaml"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.split()


def test_release_tree_collects_cleanly_with_pytest(tmp_path):
    paths = _config_files() + _archive_paths()

    tree_dir = tmp_path / "release_tree"
    tree_dir.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", "HEAD", "--", *paths],
        capture_output=True, check=True,
    )
    subprocess.run(["tar", "-xf", "-", "-C", str(tree_dir)], input=archive.stdout, check=True)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    # Deliberately not a merged os.environ: a clean environment (equivalent to `env -i` plus
    # these three) so nothing outside the release tree (an ambient PYTHONPATH, a stray
    # conftest.py picked up from elsewhere, etc.) can mask a real gap in ARCHIVE_PATHS.
    clean_env = {"HOME": str(home_dir), "PATH": "/usr/bin:/bin", "JARVIS_DEPLOYMENT": "local"}

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=tree_dir, env=clean_env, capture_output=True, text=True, timeout=120,
    )

    failure_detail = (
        "pytest --collect-only failed inside the exact release tree (ARCHIVE_PATHS in "
        f"scripts/release.sh is likely missing a file some test needs to read):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert result.returncode == 0, failure_detail
    # Belt and suspenders on top of the exit code: pytest's own collection-error summary line,
    # not a bare "error" substring match (which false-positives on legitimately named tests like
    # test_server_error_redacted).
    assert "error during collection" not in result.stdout.lower(), failure_detail
    assert "errors during collection" not in result.stdout.lower(), failure_detail
