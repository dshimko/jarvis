"""PLAN.md AD40: ofw-mcp packaging and the two new SSM documents.

Three independent things, all Linux/bash-only (skipped on Windows):

1. `scripts/release.sh`'s ofw-mcp packaging (`want_ofw_mcp`/`package_ofw_mcp`). release.sh always
   derives its own REPO_ROOT from its script path (not test-overridable), and `build_tarball`'s
   `git archive HEAD` depends on this checkout's current commit having every ARCHIVE_PATHS entry
   tracked -- true of a released checkout, not necessarily true mid-work on this branch, and not
   an ofw-mcp concern either way. So these tests `source` release.sh (its trailing
   `[ "${BASH_SOURCE[0]}" = "$0" ]` guard means sourcing never runs `main`) and call
   `want_ofw_mcp`/`package_ofw_mcp` directly against a fresh, empty, real tar file -- exercising
   the actual bash and the actual `uv build`/`git clone` calls, isolated from build_tarball. Gate
   fix (item 7): `OFW_MCP_SHA_FILE` is overridable (`JARVIS_OFW_MCP_SHA_FILE`), so these tests
   point it at a file under `tmp_path` and never touch this real checkout's `deps/ofw-mcp.sha` at
   all.
2. `ops/aws/ssm/jarvis-ofw-login.sh` against PATH shims for `systemctl` and `runuser`, and a fake
   `ofw-mcp` binary standing in for the real CLI's exit codes (0 ok, 2 timeout, EXIT_OK/EXIT_TIMEOUT
   in ofw-mcp's own `manual_login.py`).
3. `ops/aws/ssm/jarvis-ofw-reset.sh` against the same kind of shims plus `curl`/the real `jq`.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="release.sh and ops/aws/ssm scripts are Linux/bash-only"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release.sh"
OFW_LOGIN_SCRIPT = REPO_ROOT / "ops" / "aws" / "ssm" / "jarvis-ofw-login.sh"
OFW_RESET_SCRIPT = REPO_ROOT / "ops" / "aws" / "ssm" / "jarvis-ofw-reset.sh"

UNKNOWN_SHA = "f" * 40


def _write_shim(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# -- Part 1: scripts/release.sh ofw-mcp packaging --------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )


def _make_ofw_mcp_repo(tmp_path: Path, *, with_lock: bool = True) -> tuple[Path, str]:
    """A scratch git repo with a minimal setuptools-buildable package: pyproject.toml,
    (optionally) requirements-lock.txt, and a tests/ directory -- exactly what
    scripts/release.sh's package_ofw_mcp() requires of an ofw-mcp checkout."""
    repo = tmp_path / "ofw-mcp-src"
    (repo / "src" / "fakepkg").mkdir(parents=True)
    (repo / "src" / "fakepkg" / "__init__.py").write_text("def hello():\n    return 'hi'\n")
    (repo / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools>=61"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "fakepkg"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.9"\n\n'
        "[tool.setuptools.packages.find]\n"
        'where = ["src"]\n'
    )
    if with_lock:
        (repo / "requirements-lock.txt").write_text("# fake hash-pinned lock file\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")

    _git(repo, "init", "--quiet")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "--no-gpg-sign", "-m", "initial")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, sha


@pytest.fixture
def release_env(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    tar_path = tmp_path / "jarvis-test.tar"
    with tarfile.open(tar_path, "w"):
        pass  # a real, empty, uncompressed tar -- package_ofw_mcp appends to it with mode "a"
    sha_file = tmp_path / "ofw-mcp.sha"  # item 7: never the real deps/ofw-mcp.sha

    env = {
        **os.environ,
        "JARVIS_RELEASE_BUILD_DIR": str(build_dir),
        "JARVIS_OFW_MCP_SHA_FILE": str(sha_file),
    }
    return {"env": env, "build_dir": build_dir, "tar_path": tar_path, "sha_file": sha_file}


def _run_ofw_mcp_packaging(env: dict, tar_path: Path) -> subprocess.CompletedProcess:
    """Sources release.sh (never runs its `main`, see the module docstring) and calls
    want_ofw_mcp/package_ofw_mcp directly against tar_path."""
    driver = (
        "set -euo pipefail\n"
        f"source '{RELEASE_SCRIPT}'\n"
        "if want_ofw_mcp; then\n"
        f"  package_ofw_mcp '{tar_path}'\n"
        "fi\n"
    )
    return subprocess.run(["bash", "-c", driver], env=env, capture_output=True, text=True, timeout=60)


def test_missing_sha_file_refuses_without_skip(release_env):
    result = _run_ofw_mcp_packaging(release_env["env"], release_env["tar_path"])

    assert result.returncode != 0
    assert "ofw-mcp.sha" in (result.stdout + result.stderr)


def test_missing_sha_file_with_skip_produces_no_ofw_mcp_dir(release_env):
    env = {**release_env["env"], "OFW_MCP_SKIP": "1"}

    result = _run_ofw_mcp_packaging(env, release_env["tar_path"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OFW_MCP_SKIP=1" in (result.stdout + result.stderr)
    with tarfile.open(release_env["tar_path"]) as tf:
        names = tf.getnames()
    assert not any(n == "ofw-mcp" or n.startswith("ofw-mcp/") for n in names)


def test_skip_wins_even_when_sha_file_is_present(release_env, tmp_path):
    """Gate fix (item 8): OFW_MCP_SKIP=1 must skip packaging unconditionally, not only when the
    sha file also happens to be missing."""
    repo, sha = _make_ofw_mcp_repo(tmp_path)
    release_env["sha_file"].write_text(sha + "\n")
    env = {**release_env["env"], "OFW_MCP_SRC": str(repo), "OFW_MCP_SKIP": "1"}

    result = _run_ofw_mcp_packaging(env, release_env["tar_path"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OFW_MCP_SKIP=1" in (result.stdout + result.stderr)
    with tarfile.open(release_env["tar_path"]) as tf:
        names = tf.getnames()
    assert not any(n == "ofw-mcp" or n.startswith("ofw-mcp/") for n in names)


def test_unknown_sha_refused(release_env, tmp_path):
    repo, _sha = _make_ofw_mcp_repo(tmp_path)
    release_env["sha_file"].write_text(UNKNOWN_SHA + "\n")
    env = {**release_env["env"], "OFW_MCP_SRC": str(repo)}

    result = _run_ofw_mcp_packaging(env, release_env["tar_path"])

    assert result.returncode != 0
    assert "unknown ofw-mcp sha" in (result.stdout + result.stderr)


def test_missing_lock_file_refused(release_env, tmp_path):
    repo, sha = _make_ofw_mcp_repo(tmp_path, with_lock=False)
    release_env["sha_file"].write_text(sha + "\n")
    env = {**release_env["env"], "OFW_MCP_SRC": str(repo)}

    result = _run_ofw_mcp_packaging(env, release_env["tar_path"])

    assert result.returncode != 0
    assert "requirements-lock.txt" in (result.stdout + result.stderr)


def test_tarball_contains_wheel_lock_pyproject_and_tests(release_env, tmp_path):
    repo, sha = _make_ofw_mcp_repo(tmp_path)
    release_env["sha_file"].write_text(sha + "\n")
    env = {**release_env["env"], "OFW_MCP_SRC": str(repo)}

    result = _run_ofw_mcp_packaging(env, release_env["tar_path"])

    assert result.returncode == 0, result.stdout + result.stderr
    with tarfile.open(release_env["tar_path"]) as tf:
        names = tf.getnames()
        infos = {i.name: i for i in tf.getmembers()}
    assert any(n.startswith("ofw-mcp/wheels/") and n.endswith(".whl") for n in names), names
    assert "ofw-mcp/requirements-lock.txt" in names
    assert "ofw-mcp/pyproject.toml" in names
    assert "ofw-mcp/tests/test_smoke.py" in names

    # item 12: modes normalized like git archive (0644 files, 0755 dirs), not whatever the
    # scratch clone/build happened to leave on disk.
    assert infos["ofw-mcp/requirements-lock.txt"].mode == 0o644
    assert infos["ofw-mcp/tests/test_smoke.py"].mode == 0o644
    assert infos["ofw-mcp"].mode == 0o755
    assert infos["ofw-mcp/tests"].mode == 0o755


def test_failed_build_cleans_up_the_scratch_clone(release_env, tmp_path):
    """Gate fix (item 12): a failed `uv build` must not leak the scratch clone under BUILD_DIR."""
    repo, sha = _make_ofw_mcp_repo(tmp_path)
    (repo / "pyproject.toml").write_text("this is not valid pyproject.toml (:\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "--no-gpg-sign", "--amend", "-m", "break the build")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    release_env["sha_file"].write_text(sha + "\n")
    env = {**release_env["env"], "OFW_MCP_SRC": str(repo)}

    result = _run_ofw_mcp_packaging(env, release_env["tar_path"])

    assert result.returncode != 0
    leftover = list(release_env["build_dir"].glob("ofw-mcp-src.*"))
    assert leftover == [], f"scratch clone(s) not cleaned up: {leftover}"


# -- Part 2: ops/aws/ssm/jarvis-ofw-login.sh --------------------------------------------------


@pytest.fixture
def ofw_login_env(tmp_path):
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    runuser_log = tmp_path / "runuser.log"

    _write_shim(shim_dir, "systemctl", f'printf "%s\\n" "$*" >> "{systemctl_log}"\nexit 0\n')
    _write_shim(
        shim_dir,
        "runuser",
        (
            f'printf "%s\\n" "$*" >> "{runuser_log}"\n'
            "shift; shift\n"
            '[ "${1:-}" = "--" ] && shift\n'
            'exec "$@"\n'
        ),
    )

    ofw_bin_dir = tmp_path / "ofw-venv" / "bin"
    ofw_bin_dir.mkdir(parents=True)
    exit_code_file = tmp_path / "ofw_mcp_exit_code"
    _write_shim(
        ofw_bin_dir,
        "ofw-mcp",
        (
            'echo "OFW-CONTENT-MUST-NEVER-LEAK stdout"\n'
            'echo "OFW-CONTENT-MUST-NEVER-LEAK stderr" >&2\n'
            f'if [ -f "{exit_code_file}" ]; then exit "$(cat "{exit_code_file}")"; fi\n'
            "exit 0\n"
        ),
    )
    ofw_bin = ofw_bin_dir / "ofw-mcp"

    env = {
        **os.environ,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "JARVIS_OFW_MCP_BIN": str(ofw_bin),
    }
    return {
        "env": env,
        "systemctl_log": systemctl_log,
        "runuser_log": runuser_log,
        "exit_code_file": exit_code_file,
        "ofw_bin": ofw_bin,
    }


def _run_ofw_login(env: dict, wait_seconds: str = "5") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(OFW_LOGIN_SCRIPT)], env={**env, "WAIT_SECONDS": wait_seconds},
        capture_output=True, text=True, timeout=30,
    )


def test_state_saved_on_success(ofw_login_env):
    result = _run_ofw_login(ofw_login_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "state saved" in result.stdout
    assert "OFW-CONTENT-MUST-NEVER-LEAK" not in (result.stdout + result.stderr)


def test_timeout_status_on_exit_code_2(ofw_login_env):
    ofw_login_env["exit_code_file"].write_text("2")

    result = _run_ofw_login(ofw_login_env["env"])

    assert result.returncode == 2
    assert "timeout" in result.stdout
    assert "state saved" not in result.stdout
    assert "OFW-CONTENT-MUST-NEVER-LEAK" not in (result.stdout + result.stderr)


def test_failed_status_on_other_nonzero_exit(ofw_login_env):
    ofw_login_env["exit_code_file"].write_text("17")

    result = _run_ofw_login(ofw_login_env["env"])

    assert result.returncode == 17
    assert "failed" in result.stdout


def test_unit_always_stopped_then_started(ofw_login_env):
    result = _run_ofw_login(ofw_login_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    log = ofw_login_env["systemctl_log"].read_text()
    assert "stop ofw-mcp.service" in log
    assert "start ofw-mcp.service" in log
    assert log.index("stop ofw-mcp.service") < log.index("start ofw-mcp.service")


def test_unit_started_again_even_on_timeout(ofw_login_env):
    ofw_login_env["exit_code_file"].write_text("2")

    result = _run_ofw_login(ofw_login_env["env"])

    assert result.returncode == 2
    assert "start ofw-mcp.service" in ofw_login_env["systemctl_log"].read_text()


def test_runuser_used_as_jarvis_ofw_with_expected_env(ofw_login_env):
    result = _run_ofw_login(ofw_login_env["env"], wait_seconds="42")

    assert result.returncode == 0, result.stdout + result.stderr
    log = ofw_login_env["runuser_log"].read_text()
    assert "-u jarvis-ofw --" in log
    assert "HOME=/home/jarvis-ofw" in log
    assert "PLAYWRIGHT_BROWSERS_PATH=/home/jarvis-ofw/.cache/ms-playwright" in log
    assert "OFW_MCP_PROFILE=aws" in log
    assert "OFW_TZ=America/Detroit" in log
    assert "login --wait 42" in log


def test_bad_wait_seconds_refused_before_touching_the_unit(ofw_login_env):
    result = _run_ofw_login(ofw_login_env["env"], wait_seconds="99999")

    assert result.returncode != 0
    assert "WaitSeconds" in (result.stdout + result.stderr)
    assert not ofw_login_env["systemctl_log"].exists()


def test_non_numeric_wait_seconds_refused(ofw_login_env):
    result = _run_ofw_login(ofw_login_env["env"], wait_seconds="abc")

    assert result.returncode != 0
    assert not ofw_login_env["systemctl_log"].exists()


def test_wait_seconds_above_3600_refused_by_shape_valid_value(ofw_login_env):
    """Gate fix (item 4): "3601" matches the Terraform allowedPattern's shape (1-4 digits) but
    exceeds ofw-mcp's own MAX_LOGIN_WAIT_SECONDS=3600 (cli.py) -- outside that bound the CLI
    itself would exit 2, the SAME code as a genuine timeout, so this must be caught here instead
    of ever reaching the CLI."""
    result = _run_ofw_login(ofw_login_env["env"], wait_seconds="3601")

    assert result.returncode != 0
    assert "1 to 3600" in (result.stdout + result.stderr)
    assert not ofw_login_env["systemctl_log"].exists()


def test_wait_seconds_zero_refused(ofw_login_env):
    result = _run_ofw_login(ofw_login_env["env"], wait_seconds="0")

    assert result.returncode != 0
    assert "1 to 3600" in (result.stdout + result.stderr)
    assert not ofw_login_env["systemctl_log"].exists()


def test_wait_seconds_at_the_3600_bound_accepted(ofw_login_env):
    result = _run_ofw_login(ofw_login_env["env"], wait_seconds="3600")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "state saved" in result.stdout


def test_missing_binary_refused(ofw_login_env):
    ofw_login_env["ofw_bin"].unlink()

    result = _run_ofw_login(ofw_login_env["env"])

    assert result.returncode != 0
    assert "not found" in (result.stdout + result.stderr)
    assert not ofw_login_env["systemctl_log"].exists()


def test_ofw_login_script_has_no_double_curly_braces():
    assert "{{" not in OFW_LOGIN_SCRIPT.read_text(encoding="utf-8")


# -- Part 3: ops/aws/ssm/jarvis-ofw-reset.sh ---------------------------------------------------


@pytest.fixture
def ofw_reset_env(tmp_path):
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    breaker_file = state_dir / "breaker.json"
    breaker_file.write_text('{"opened_at": "x", "reason": "OFWLoginError"}')

    runuser_log = tmp_path / "runuser.log"
    active_marker = tmp_path / "unit_active"
    healthz_body_file = tmp_path / "healthz_body"
    healthz_body_file.write_text('{"ok": true, "breaker": "closed"}')
    curl_should_fail = tmp_path / "curl_should_fail"

    _write_shim(
        shim_dir,
        "runuser",
        (
            f'printf "%s\\n" "$*" >> "{runuser_log}"\n'
            "shift; shift\n"
            '[ "${1:-}" = "--" ] && shift\n'
            'exec "$@"\n'
        ),
    )
    _write_shim(
        shim_dir,
        "systemctl",
        (
            'if [ "$1" = "is-active" ]; then\n'
            f'  [ -f "{active_marker}" ] && exit 0\n'
            "  exit 3\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    _write_shim(
        shim_dir,
        "curl",
        (
            f'[ -f "{curl_should_fail}" ] && exit 22\n'
            f'cat "{healthz_body_file}"\n'
        ),
    )

    env = {
        **os.environ,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "JARVIS_OFW_STATE_DIR": str(state_dir),
    }
    return {
        "env": env,
        "breaker_file": breaker_file,
        "runuser_log": runuser_log,
        "active_marker": active_marker,
        "healthz_body_file": healthz_body_file,
        "curl_should_fail": curl_should_fail,
    }


def _run_ofw_reset(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(OFW_RESET_SCRIPT)], env=env, capture_output=True, text=True, timeout=15)


def test_breaker_file_removed_as_jarvis_ofw(ofw_reset_env):
    result = _run_ofw_reset(ofw_reset_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert not ofw_reset_env["breaker_file"].exists()
    log = ofw_reset_env["runuser_log"].read_text()
    assert "-u jarvis-ofw --" in log
    assert "rm -f" in log
    assert "breaker.json" in log


def test_unit_inactive_prints_unknown_without_curl(ofw_reset_env):
    result = _run_ofw_reset(ofw_reset_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "breaker: unknown" in result.stdout


def test_unit_active_prints_closed(ofw_reset_env):
    ofw_reset_env["active_marker"].write_text("1")

    result = _run_ofw_reset(ofw_reset_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "breaker: closed" in result.stdout


def test_unit_active_prints_open(ofw_reset_env):
    ofw_reset_env["active_marker"].write_text("1")
    ofw_reset_env["healthz_body_file"].write_text('{"ok": true, "breaker": "open"}')

    result = _run_ofw_reset(ofw_reset_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "breaker: open" in result.stdout


def test_unit_active_but_curl_fails_prints_unknown(ofw_reset_env):
    ofw_reset_env["active_marker"].write_text("1")
    ofw_reset_env["curl_should_fail"].write_text("1")

    result = _run_ofw_reset(ofw_reset_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "breaker: unknown" in result.stdout


def test_ofw_reset_script_has_no_double_curly_braces():
    assert "{{" not in OFW_RESET_SCRIPT.read_text(encoding="utf-8")
