"""Brief 9.5 / DESIGN.md 8.3: jarvis-deploy.sh rolls back to the previous release when the new
release's health check fails, and switches `current` when it passes.

Runs the real ops/aws/ssm/jarvis-deploy.sh (and the real ops/aws/lib/install-release.sh it
sources from each release) under PATH shims for systemctl, aws, tailscale, curl, runuser, id,
chown, useradd, flock, sha256sum, pip, python3.12, and pytest, against a temp JARVIS_ROOT (and a
temp JARVIS_HOME_DIR standing in for /home). Skipped on Windows: the script is bash-only.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="ops/aws/ssm scripts are Linux/bash-only"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ops" / "aws" / "ssm" / "jarvis-deploy.sh"
INSTALL_RELEASE_LIB = REPO_ROOT / "ops" / "aws" / "lib" / "install-release.sh"
REQUIREMENTS_LOCK = REPO_ROOT / "requirements-lock.txt"

# Release identity is the full 40-character git sha everywhere (coordinator decision): matches
# jarvis-deploy.sh's own re-validation regex, which now equals the SSM document's allowedPattern.
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40
SHA_E = "e" * 40


def _write_shim(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_release_tarball(
    bucket: Path, sha: str, *, include_ofw: bool = False, ofw_wheel_count: int = 1
) -> None:
    """Builds a minimal, valid release tree under a scratch dir and tars it into `bucket`,
    matching the layout scripts/release.sh produces: a top-level ops/aws/ with something in every
    subdirectory jarvis-deploy.sh (via the real ops/aws/lib/install-release.sh, copied in here
    verbatim so the test exercises the actual file) installs, plus requirements-lock.txt for the
    fake hash-pinned venv build. `jarvis-status`'s content is sha-specific so a test can tell
    which release actually got (re)installed.

    ofw-mcp.service and the jarvis-logexport@ofw.service.d/ drop-in are always present (bootstrap
    ships both regardless of whether a given release also carries the ofw-mcp wheel bundle,
    PLAN.md AD40/AD37) -- exercising install-release.sh's generic drop-in loop and its
    setup_users() call on every deploy, not just ofw-mcp ones. lib/users.sh here is a fake
    (install-release.sh only needs to source it and call setup_users -- the real production
    users.sh is bootstrap-engineer's own file and its own tests' concern, not testable from here
    without root and a real Linux box, AD31). The optional ofw-mcp/ directory (wheel + lock +
    pyproject + tests) is only added when include_ofw is set (PLAN.md AD40: "a release without
    ofw-mcp/ skips all of this with one log line")."""
    src = bucket / f"src-{sha}"
    for sub in ("bin", "libexec", "systemd", "cloudwatch", "logrotate", "iptables", "lib", "ssm"):
        (src / "ops" / "aws" / sub).mkdir(parents=True)
    (src / "ops" / "aws" / "bin" / "jarvis-status").write_text(f"#!/usr/bin/env bash\necho ok-{sha}\n")
    (src / "ops" / "aws" / "libexec" / "jarvis-vault-commit").write_text("#!/usr/bin/env bash\n")
    (src / "ops" / "aws" / "systemd" / "jarvis@.service").write_text("[Unit]\n")
    (src / "ops" / "aws" / "systemd" / "ofw-mcp.service").write_text("[Unit]\n")
    (src / "ops" / "aws" / "cloudwatch" / "amazon-cloudwatch-agent.json").write_text("{}")
    (src / "ops" / "aws" / "logrotate" / "jarvis").write_text("")
    (src / "ops" / "aws" / "iptables" / "rules.v4").write_text("*filter\nCOMMIT\n")
    (src / "ops" / "aws" / "iptables" / "rules.v6").write_text("*filter\nCOMMIT\n")
    (src / "ops" / "aws" / "ssm" / "jarvis-deploy.sh").write_text("#!/usr/bin/env bash\n# placeholder\n")
    (src / "ops" / "aws" / "lib" / "install-release.sh").write_text(
        INSTALL_RELEASE_LIB.read_text(encoding="utf-8")
    )
    dropin_dir = src / "ops" / "aws" / "systemd" / "jarvis-logexport@ofw.service.d"
    dropin_dir.mkdir(parents=True)
    (dropin_dir / "unit.conf").write_text(f"[Service]\nEnvironment=JARVIS_LOGEXPORT_UNIT=ofw-mcp.service\n# {sha}\n")
    (src / "ops" / "aws" / "lib" / "users.sh").write_text(
        "setup_users() {\n"
        f'  printf "setup_users called for {sha}\\n" >> "${{JARVIS_TEST_SETUP_USERS_LOG:?}}"\n'
        # Item 2: the marker the fake `runuser` shim checks before allowing "-u jarvis-ofw"
        # through -- proves setup_users runs before the first runuser call into that user.
        '  : > "${JARVIS_TEST_OFW_USER_MARKER:?}"\n'
        "}\n"
    )
    (src / "requirements-lock.txt").write_text("")

    if include_ofw:
        ofw_dir = src / "ofw-mcp"
        (ofw_dir / "wheels").mkdir(parents=True)
        for i in range(ofw_wheel_count):
            (ofw_dir / "wheels" / f"ofw_mcp-0.1.{i}-py3-none-any.whl").write_bytes(b"fake-wheel")
        (ofw_dir / "requirements-lock.txt").write_text("")
        (ofw_dir / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n")
        (ofw_dir / "tests").mkdir()
        (ofw_dir / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")

    release_dir = bucket / "releases" / sha
    release_dir.mkdir(parents=True)
    tgz = release_dir / f"jarvis-{sha}.tar.gz"
    with tarfile.open(tgz, "w:gz") as tar:
        tar.add(src, arcname=".")
    digest = hashlib.sha256(tgz.read_bytes()).hexdigest()
    (release_dir / f"jarvis-{sha}.tar.gz.sha256").write_text(f"{digest}  jarvis-{sha}.tar.gz\n")


@pytest.fixture
def deploy_env(tmp_path):
    """Builds JARVIS_ROOT, a fake S3 "bucket" directory holding releases A and B, fake mode homes
    with a token each, PATH shims, and the env dict to run jarvis-deploy.sh with. The `curl` shim
    fails (mimicking a 500) whenever the `health_should_fail` marker file exists; the generated
    `pytest` inside the fake venv fails whenever `pytest_should_fail` exists."""
    root = tmp_path / "opt-jarvis"
    (root / "releases").mkdir(parents=True)

    home = tmp_path / "home"
    for mode in ("work", "personal"):
        d = home / f"jarvis-{mode}" / ".jarvis"
        d.mkdir(parents=True)
        (d / "api_token").write_text(f"tok-{mode}\n")

    bucket = tmp_path / "bucket"
    bucket.mkdir()
    _make_release_tarball(bucket, SHA_A)
    _make_release_tarball(bucket, SHA_B)

    systemctl_log = tmp_path / "systemctl.log"
    health_fail_marker = tmp_path / "health_should_fail"
    pytest_fail_marker = tmp_path / "pytest_should_fail"
    curl_calls_log = tmp_path / "curl_calls.log"
    curl_headers_log = tmp_path / "curl_headers.log"
    runuser_calls_log = tmp_path / "runuser_calls.log"
    # AD40 additions: a second pytest fail marker (ofw-venv's own suite, independent of the
    # Jarvis suite's marker above), the ofw-mcp.service systemd condition state (default unset ->
    # "ConditionResult=no", i.e. not deployed yet), an ofw-specific /healthz failure switch (kept
    # separate from health_fail_marker, which fails every curl call including the two mode ones),
    # and a playwright call log (both the root install-deps call and the runuser-wrapped browser
    # install land here; runuser_calls_log is what distinguishes which ran as jarvis-ofw).
    ofw_pytest_fail_marker = tmp_path / "ofw_pytest_should_fail"
    ofw_deployed_marker = tmp_path / "ofw_mcp_condition_result_yes"
    ofw_health_fail_marker = tmp_path / "ofw_health_should_fail"
    playwright_calls_log = tmp_path / "playwright_calls.log"
    playwright_should_fail = tmp_path / "playwright_should_fail"
    setup_users_log = tmp_path / "setup_users.log"
    # Item 2: unset until the fake setup_users() (built into every release, above) has run; the
    # runuser shim below refuses "-u jarvis-ofw" while it is absent, so a test can prove
    # ensure_ofw_user() (build_and_test_ofw) runs setup_users before its first runuser call.
    ofw_user_marker = tmp_path / "ofw_user_created"
    pip_calls_log = tmp_path / "pip_calls.log"

    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()

    _write_shim(
        shim_dir,
        "playwright",
        f'printf "%s\\n" "$*" >> "{playwright_calls_log}"\n[ -f "{playwright_should_fail}" ] && exit 1\nexit 0\n',
    )

    _write_shim(shim_dir, "id", 'if [ "$2" = "jarvis-build" ]; then echo 0; exit 0; fi\nexit 1\n')
    _write_shim(shim_dir, "useradd", "exit 0\n")
    _write_shim(shim_dir, "chown", "exit 0\n")
    _write_shim(shim_dir, "flock", "exit 0\n")  # H1: single-process test, an uncontended lock always succeeds
    _write_shim(
        shim_dir,
        "runuser",
        # H1/M9: log every call (mode/user + full remaining argv) before exec'ing, so tests can
        # confirm the per-mode token is read via runuser (never a root path traversal, AD31) and
        # that jarvis-build is used for the test run, without capturing the token value itself.
        # Item 2: refuses "-u jarvis-ofw" outright (simulating a real `runuser: user jarvis-ofw
        # does not exist`) until the fake setup_users() has touched ofw_user_marker, so a test can
        # prove build_and_test_ofw ensures the user exists before its first runuser -u jarvis-ofw
        # step, not only later via install_release_ops_aws.
        (
            f'printf "%s\\n" "$*" >> "{runuser_calls_log}"\n'
            f'if [ "$2" = "jarvis-ofw" ] && [ ! -f "{ofw_user_marker}" ]; then\n'
            '  echo "runuser: user jarvis-ofw does not exist" >&2\n'
            "  exit 1\n"
            "fi\n"
            'shift\nshift\n[ "${1:-}" = "--" ] && shift\nexec "$@"\n'
        ),
    )
    _write_shim(shim_dir, "tailscale", 'if [ "$1" = "ip" ]; then echo 100.64.1.2; exit 0; fi\nexit 1\n')
    _write_shim(
        shim_dir,
        "systemctl",
        (
            f'echo "systemctl $*" >> "{systemctl_log}"\n'
            'if [ "$1" = "show" ] && [ "$2" = "ofw-mcp.service" ]; then\n'
            f'  if [ -f "{ofw_deployed_marker}" ]; then echo "ConditionResult=yes"; else echo "ConditionResult=no"; fi\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    # No mv or sha256sum shims needed: jarvis-deploy.sh's own switch_current() uses `ln -sfn` +
    # `python3 -c 'os.replace(...)'` (portable rename, not GNU mv -T) and sha256_check() falls
    # back to `shasum -a 256` when sha256sum isn't on PATH, which is the case on macOS.
    _write_shim(shim_dir, "pip", f'echo "$*" >> "{pip_calls_log}"\nexit 0\n')
    _write_shim(
        shim_dir,
        "python3.12",
        (
            'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
            '  mkdir -p "$3/bin"\n'
            # Gate fix (item 9): logs every install invocation (so a test can assert
            # --require-hashes / --no-deps were passed) instead of a bare no-op. `echo`, not
            # `printf "%s\n"`, so the generated pip script needs no escaping of its own.
            "  {\n"
            "    printf '#!/usr/bin/env bash\\n'\n"
            f'    printf \'echo "$*" >> "{pip_calls_log}"\\n\'\n'
            "    printf 'exit 0\\n'\n"
            '  } > "$3/bin/pip"\n'
            "  {\n"
            "    printf '#!/usr/bin/env bash\\n'\n"
            # AD40: the SAME generated pytest script lands in both .venv/bin and ofw-venv/bin
            # (both are created by "python3.12 -m venv <path>"); it tells the two suites apart by
            # its own invocation path ($0), so each venv's pytest honors its own fail marker only.
            "    printf 'case \"$0\" in\\n'\n"
            f'    printf \'  */ofw-venv/*) [ -f "{ofw_pytest_fail_marker}" ] && exit 1 ;;\\n\'\n'
            f'    printf \'  *) [ -f "{pytest_fail_marker}" ] && exit 1 ;;\\n\'\n'
            "    printf 'esac\\n'\n"
            "    printf 'exit 0\\n'\n"
            '  } > "$3/bin/pytest"\n'
            f'  cp "{shim_dir}/playwright" "$3/bin/playwright"\n'
            '  chmod +x "$3/bin/pip" "$3/bin/pytest" "$3/bin/playwright"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n"
        ),
    )
    _write_shim(
        shim_dir,
        "aws",
        (
            'if [ "$1" = "s3" ] && [ "$2" = "cp" ]; then\n'
            '  rel="${3#s3://}"; rel="${rel#*/}"\n'
            f'  cp "{bucket}/$rel" "$4"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n"
        ),
    )
    _write_shim(
        shim_dir,
        "curl",
        (
            # M9: log full argv (never the token itself -- the token only ever appears inside
            # the header FILE, referenced here as "@<path>", never as a literal in argv) plus a
            # separate copy of whatever header file content curl was told to send, so a test can
            # assert the token appears in the latter and never in the former.
            f'printf "%s\\n" "$*" >> "{curl_calls_log}"\n'
            'prev=""; hdrfile=""\n'
            'for a in "$@"; do\n'
            '  if [ "$prev" = "-H" ]; then case "$a" in "@"*) hdrfile="${a#@}" ;; esac; fi\n'
            '  prev="$a"\n'
            "done\n"
            f'[ -n "$hdrfile" ] && [ -f "$hdrfile" ] && cat "$hdrfile" >> "{curl_headers_log}"\n'
            'for a in "$@"; do url="$a"; done\n'
            "case \"$url\" in\n"
            "  *:8783/healthz)\n"
            f'    [ -f "{ofw_health_fail_marker}" ] && exit 22\n'
            "    printf '{\"ok\": true, \"breaker\": \"closed\"}'\n"
            "    exit 0\n"
            "    ;;\n"
            "esac\n"
            f'if [ -f "{health_fail_marker}" ]; then exit 22; fi\n'
            'mode=work; case "$url" in *:8782/*) mode=personal ;; esac\n'
            'printf \'{"ok": true, "mode": "%s", "deployment": "aws"}\' "$mode"\n'
        ),
    )

    env = {
        **os.environ,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "JARVIS_ROOT": str(root),
        "JARVIS_HOME_DIR": str(home),
        "JARVIS_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "JARVIS_IPTABLES_DIR": str(tmp_path / "iptables"),
        "JARVIS_RUN_DIR": str(tmp_path / "run-jarvis"),
        "JARVIS_CW_CONFIG_DIR": str(tmp_path / "cw"),
        "JARVIS_LOGROTATE_DIR": str(tmp_path / "logrotate"),
        "JARVIS_ARTIFACTS_BUCKET": "test-bucket",
        "JARVIS_REGION": "us-east-1",
        "JARVIS_HEALTH_TIMEOUT_S": "2",
        "JARVIS_HEALTH_INTERVAL_S": "0.2",
        # H1: a fixed real path (e.g. /run/jarvis-deploy.lock) would collide with a real deploy
        # already holding it when this suite itself runs on-box (as jarvis-build, inside
        # jarvis-deploy.sh's own build_and_test step) -- always scope the lock to this test.
        "JARVIS_DEPLOY_LOCK": str(tmp_path / "deploy.lock"),
        # AD40: read by the fake ops/aws/lib/users.sh built into every release tree above.
        "JARVIS_TEST_SETUP_USERS_LOG": str(setup_users_log),
        "JARVIS_TEST_OFW_USER_MARKER": str(ofw_user_marker),
    }
    return {
        "env": env,
        "root": root,
        "bucket": bucket,
        "systemctl_log": systemctl_log,
        "health_fail_marker": health_fail_marker,
        "pytest_fail_marker": pytest_fail_marker,
        "curl_calls_log": curl_calls_log,
        "curl_headers_log": curl_headers_log,
        "runuser_calls_log": runuser_calls_log,
        "ofw_pytest_fail_marker": ofw_pytest_fail_marker,
        "ofw_deployed_marker": ofw_deployed_marker,
        "ofw_health_fail_marker": ofw_health_fail_marker,
        "playwright_calls_log": playwright_calls_log,
        "playwright_should_fail": playwright_should_fail,
        "setup_users_log": setup_users_log,
        "ofw_user_marker": ofw_user_marker,
        "pip_calls_log": pip_calls_log,
    }


def _run_deploy(sha: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)], env={**env, "SHA": sha}, capture_output=True, text=True, timeout=60
    )


def test_healthy_release_switches_current(deploy_env):
    result = _run_deploy(SHA_A, deploy_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    current = deploy_env["root"] / "current"
    assert current.is_symlink()
    assert os.path.basename(os.readlink(current)) == SHA_A
    assert "jarvis@work" in deploy_env["systemctl_log"].read_text()


def test_failing_health_check_rolls_back_to_previous_release(deploy_env):
    env = deploy_env["env"]
    first = _run_deploy(SHA_A, env)
    assert first.returncode == 0, first.stdout + first.stderr

    deploy_env["health_fail_marker"].write_text("fail")
    second = _run_deploy(SHA_B, env)

    assert second.returncode != 0
    current = deploy_env["root"] / "current"
    assert os.path.basename(os.readlink(current)) == SHA_A, "current must still point at the healthy release"
    # The health_fail_marker used to force B's failure is still present during rollback's own
    # re-check of A, so that re-check fails too here; either outcome message is a valid signal
    # that rollback ran (LOW item: only the "rolled_back" spelling claims a passing health check).
    output = second.stdout + second.stderr
    assert "rolled_back" in output or "rollback_health_check_failed" in output


def test_rollback_logs_rolled_back_only_when_its_own_health_check_passes(deploy_env, tmp_path):
    """LOW: distinguishes the two rollback outcomes -- this scenario clears the health-fail
    marker before the health check that matters is reached, so rollback's re-check of the
    (good) previous release passes and the script must say so honestly."""
    env = deploy_env["env"]
    first = _run_deploy(SHA_A, env)
    assert first.returncode == 0, first.stdout + first.stderr

    # A curl shim that fails only for the sha currently being switched TO (SHA_B), so B's own
    # deploy health check fails but a subsequent rollback health check (still serving A, since
    # `current` only ever briefly points at B) succeeds once restarted.
    calls_log = tmp_path / "curl_calls2.log"
    curl_path = Path(env["PATH"].split(":", 1)[0]) / "curl"
    curl_path.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{calls_log}"\n'
        'for a in "$@"; do url="$a"; done\n'
        'mode=work; case "$url" in *:8782/*) mode=personal ;; esac\n'
        f'if [ "$(readlink "{deploy_env["root"]}/current")" = "releases/{SHA_B}" ]; then exit 22; fi\n'
        'printf \'{"ok": true, "mode": "%s", "deployment": "aws"}\' "$mode"\n'
    )
    curl_path.chmod(curl_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    second = _run_deploy(SHA_B, env)

    assert second.returncode != 0
    output = second.stdout + second.stderr
    assert "rolled_back" in output
    assert "rollback_health_check_failed" not in output


def test_first_deploy_with_no_previous_release_stops_daemons_on_failure(deploy_env):
    """No `prev` to roll back to (first deploy ever): `current` was already switched to the new,
    unhealthy release before the health check ran, so it is left in place (a later `jarvis-deploy`
    of a good sha will then treat this one as `prev`), and both daemons are stopped instead."""
    env = deploy_env["env"]
    deploy_env["health_fail_marker"].write_text("fail")

    result = _run_deploy(SHA_A, env)

    assert result.returncode != 0
    current = deploy_env["root"] / "current"
    assert os.path.basename(os.readlink(current)) == SHA_A
    assert "systemctl stop jarvis@work jarvis@personal" in deploy_env["systemctl_log"].read_text()


def test_checksum_mismatch_touches_nothing(deploy_env):
    root = deploy_env["root"]
    env = deploy_env["env"]
    # Corrupt the sha256 sidecar in the fake bucket so the download step fails verification.
    sidecar = deploy_env["bucket"] / "releases" / SHA_A / f"jarvis-{SHA_A}.tar.gz.sha256"
    sidecar.write_text("0" * 64 + f"  jarvis-{SHA_A}.tar.gz\n")

    result = _run_deploy(SHA_A, env)

    assert result.returncode != 0
    assert not (root / "current").exists()
    assert not (root / "releases" / SHA_A).exists()


def test_pytest_failure_leaves_current_untouched_and_exits_nonzero(deploy_env):
    """M9: the on-box test-suite-failure branch never switches `current` at all (it runs before
    install_release_ops_aws / switch_current in the pipeline)."""
    deploy_env["pytest_fail_marker"].write_text("fail")

    result = _run_deploy(SHA_A, deploy_env["env"])

    assert result.returncode != 0
    assert "test suite failed" in (result.stdout + result.stderr)
    assert not (deploy_env["root"] / "current").exists()
    assert not (deploy_env["root"] / "releases" / SHA_A / ".complete").exists()


def test_bearer_token_travels_via_header_file_never_argv(deploy_env):
    """M9: the health check's Authorization header goes through a file (`curl -H @<path>`), never
    the command line -- the raw token must never appear in curl's own argv."""
    result = _run_deploy(SHA_A, deploy_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    calls = deploy_env["curl_calls_log"].read_text()
    headers = deploy_env["curl_headers_log"].read_text()
    assert "tok-work" not in calls and "tok-personal" not in calls
    assert "Authorization: Bearer tok-work" in headers
    assert "Authorization: Bearer tok-personal" in headers
    # And the header file path passed to curl always lives under the (tmpfs in production,
    # JARVIS_RUN_DIR here) run dir per DESIGN.md section 6.2, never the general system tmp dir.
    assert re.search(r"-H @\S*/run-jarvis/hdr-work\b", calls)
    assert re.search(r"-H @\S*/run-jarvis/hdr-personal\b", calls)


def test_runuser_is_used_to_read_the_per_mode_token(deploy_env):
    """M9: AD31 -- root never opens a path under /home/jarvis-<mode> directly; the token read for
    the health check goes through `runuser -u jarvis-<mode> --`."""
    result = _run_deploy(SHA_A, deploy_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    calls = deploy_env["runuser_calls_log"].read_text()
    assert "-u jarvis-work -- cat" in calls
    assert "-u jarvis-personal -- cat" in calls
    assert "api_token" in calls


def test_rollback_reinstalls_previous_release_and_restarts_both_daemons(deploy_env):
    env = deploy_env["env"]
    first = _run_deploy(SHA_A, env)
    assert first.returncode == 0, first.stdout + first.stderr

    deploy_env["health_fail_marker"].write_text("fail")
    second = _run_deploy(SHA_B, env)
    assert second.returncode != 0

    # Proves install_release_ops_aws(prev) actually ran during rollback (not just switch_current):
    # jarvis-status's content is sha-specific, and this file only comes from A's release tree.
    installed_status = (deploy_env["root"] / "bin" / "jarvis-status").read_text()
    assert f"ok-{SHA_A}" in installed_status

    restarts = deploy_env["systemctl_log"].read_text().count("restart jarvis@work jarvis@personal")
    assert restarts >= 2, "expected one restart for the failed deploy and one more during rollback"


def test_prune_keeps_exactly_the_configured_number_of_releases(deploy_env):
    bucket = deploy_env["bucket"]
    for sha in (SHA_C, SHA_D, SHA_E):
        _make_release_tarball(bucket, sha)
    env = {**deploy_env["env"], "JARVIS_KEEP_RELEASES": "2"}

    for sha in (SHA_A, SHA_B, SHA_C, SHA_D, SHA_E):
        result = _run_deploy(sha, env)
        assert result.returncode == 0, result.stdout + result.stderr

    remaining = {p.name for p in (deploy_env["root"] / "releases").iterdir() if p.is_dir()}
    assert remaining == {SHA_D, SHA_E}, remaining


def test_refuses_to_run_when_region_is_not_set(deploy_env, tmp_path):
    """AD42: region literals leave every script. With no JARVIS_REGION env var and no region
    file to fall back to, jarvis-deploy.sh must fail loudly instead of defaulting to a
    hardcoded region, and must exit before touching JARVIS_ROOT."""
    env = dict(deploy_env["env"])
    env.pop("JARVIS_REGION", None)
    env["JARVIS_REGION_FILE"] = str(tmp_path / "no-such-region-file")

    result = _run_deploy(SHA_A, env)

    assert result.returncode != 0
    assert "AWS region not set" in (result.stdout + result.stderr)
    assert not (deploy_env["root"] / "current").exists()


def test_requirements_lock_exists_and_every_pinned_entry_is_hashed():
    """M4: requirements-lock.txt (uv pip compile --generate-hashes) ships in the release tarball
    and jarvis-deploy.sh installs from it with --require-hashes; every pinned requirement line
    must carry at least one --hash entry, or pip's own --require-hashes would refuse it anyway."""
    assert REQUIREMENTS_LOCK.is_file(), "requirements-lock.txt is missing; regenerate with: uv pip compile --generate-hashes requirements-dev.txt -o requirements-lock.txt"
    lines = REQUIREMENTS_LOCK.read_text(encoding="utf-8").splitlines()
    requirement_starts = [i for i, line in enumerate(lines) if re.match(r"^[A-Za-z0-9_.\-]+==\S+ \\\s*$", line)]
    assert requirement_starts, "no pinned '<pkg>==<version> \\' entries found in requirements-lock.txt"
    for i in requirement_starts:
        j = i + 1
        hashed = False
        while j < len(lines) and lines[j].strip().startswith("--hash="):
            hashed = True
            j += 1
        assert hashed, f"requirements-lock.txt line {i + 1} ({lines[i]!r}) has no --hash"


# -- PLAN.md AD40: ofw-mcp packaging inside jarvis-deploy.sh ----------------------------------

SHA_OFW_1 = "1" * 40
SHA_OFW_2 = "2" * 40


def test_release_without_ofw_mcp_skips_ofw_packaging_cleanly(deploy_env):
    """SHA_A's release (built by the shared fixture) has no ofw-mcp/ directory at all."""
    result = _run_deploy(SHA_A, deploy_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "has no ofw-mcp/, skipping ofw-mcp packaging" in output
    assert "ofw_mcp_not_deployed_yet" in output
    assert not (deploy_env["root"] / "releases" / SHA_A / "ofw-venv").exists()
    assert not deploy_env["playwright_calls_log"].exists()


def test_ofw_venv_built_and_browser_installed_as_the_user(deploy_env):
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True)

    result = _run_deploy(SHA_OFW_1, deploy_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    release_dir = deploy_env["root"] / "releases" / SHA_OFW_1
    assert (release_dir / "ofw-venv" / "bin" / "pip").is_file()
    assert (release_dir / "ofw-venv" / "bin" / "playwright").is_file()

    playwright_calls = deploy_env["playwright_calls_log"].read_text()
    assert "install-deps chromium" in playwright_calls
    assert "install chromium" in playwright_calls

    runuser_calls = deploy_env["runuser_calls_log"].read_text().splitlines()
    ofw_lines = [ln for ln in runuser_calls if "jarvis-ofw" in ln]
    assert ofw_lines, "expected a runuser -u jarvis-ofw call (the browser install, AD31)"
    assert any("PLAYWRIGHT_BROWSERS_PATH=/home/jarvis-ofw/.cache/ms-playwright" in ln and "install chromium" in ln for ln in ofw_lines)
    # AD31/AD40: the system-deps install runs directly as root, never through runuser.
    assert not any("install-deps" in ln for ln in ofw_lines)

    # Gate fix (item 1, PLAN.md AD40 amendment): self-contained marker expression.
    assert any(
        "jarvis-build" in ln and "not browser and not live and not repo" in ln for ln in runuser_calls
    )

    # Gate fix (item 9): --require-hashes for the lock install, --no-deps for the wheel install.
    ofw_dir = deploy_env["root"] / "releases" / SHA_OFW_1 / "ofw-mcp"
    pip_calls = deploy_env["pip_calls_log"].read_text().splitlines()
    assert any(
        "install" in ln and "--require-hashes" in ln and str(ofw_dir / "requirements-lock.txt") in ln
        for ln in pip_calls
    )
    assert any("install" in ln and "--no-deps" in ln and ".whl" in ln for ln in pip_calls)


def test_ensure_ofw_user_runs_before_first_runuser_jarvis_ofw_call(deploy_env):
    """Gate fix (item 2): a live box bootstrapped before phase I has no jarvis-ofw user yet.
    build_and_test_ofw must create it (via ensure_ofw_user/setup_users) before its own first
    `runuser -u jarvis-ofw` step, not rely on install_release_ops_aws running later in main(). The
    fake runuser shim refuses "-u jarvis-ofw" until the fake setup_users() has touched the marker,
    so this fails loudly if the ordering ever regresses."""
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True)
    assert not deploy_env["ofw_user_marker"].exists()

    result = _run_deploy(SHA_OFW_1, deploy_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert deploy_env["ofw_user_marker"].exists()
    assert "-u jarvis-ofw --" in deploy_env["runuser_calls_log"].read_text()


def test_ofw_zero_wheels_refused(deploy_env):
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True, ofw_wheel_count=0)

    result = _run_deploy(SHA_OFW_1, deploy_env["env"])

    assert result.returncode != 0
    assert "expected exactly one ofw-mcp wheel" in (result.stdout + result.stderr)
    assert not (deploy_env["root"] / "current").exists()


def test_ofw_two_wheels_refused(deploy_env):
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True, ofw_wheel_count=2)

    result = _run_deploy(SHA_OFW_1, deploy_env["env"])

    assert result.returncode != 0
    assert "expected exactly one ofw-mcp wheel" in (result.stdout + result.stderr)
    assert not (deploy_env["root"] / "current").exists()


def test_ofw_unit_condition_skipped_health_check_passes_without_rollback(deploy_env):
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True)
    # ofw_deployed_marker is deliberately NOT set: ofw-mcp.service stays condition-skipped (the
    # human has not populated jarvis/ofw yet), matching a live box right after this release lands.

    result = _run_deploy(SHA_OFW_1, deploy_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ofw_mcp_not_deployed_yet" in (result.stdout + result.stderr)
    current = deploy_env["root"] / "current"
    assert os.path.basename(os.readlink(current)) == SHA_OFW_1


def test_ofw_health_failure_rolls_back_and_restarts_ofw_unit(deploy_env):
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True)
    _make_release_tarball(bucket, SHA_OFW_2, include_ofw=True)
    env = deploy_env["env"]
    deploy_env["ofw_deployed_marker"].write_text("1")

    first = _run_deploy(SHA_OFW_1, env)
    assert first.returncode == 0, first.stdout + first.stderr

    deploy_env["ofw_health_fail_marker"].write_text("fail")
    second = _run_deploy(SHA_OFW_2, env)

    assert second.returncode != 0
    current = deploy_env["root"] / "current"
    assert os.path.basename(os.readlink(current)) == SHA_OFW_1, "current must still point at the ofw-healthy release"
    output = second.stdout + second.stderr
    assert "ofw-mcp /healthz check failed" in output
    assert "rolled_back" in output or "rollback_health_check_failed" in output
    restarts = deploy_env["systemctl_log"].read_text().count("restart jarvis@work jarvis@personal ofw-mcp.service")
    assert restarts >= 2, "expected one restart for the failed deploy and one more during rollback"


def test_ofw_pytest_failure_leaves_current_untouched(deploy_env):
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True)
    deploy_env["ofw_pytest_fail_marker"].write_text("fail")

    result = _run_deploy(SHA_OFW_1, deploy_env["env"])

    assert result.returncode != 0
    assert "ofw-mcp test suite failed" in (result.stdout + result.stderr)
    assert not (deploy_env["root"] / "current").exists()


def test_playwright_install_deps_failure_fails_before_switch(deploy_env):
    bucket = deploy_env["bucket"]
    _make_release_tarball(bucket, SHA_OFW_1, include_ofw=True)
    deploy_env["playwright_should_fail"].write_text("fail")

    result = _run_deploy(SHA_OFW_1, deploy_env["env"])

    assert result.returncode != 0
    assert "playwright install-deps chromium failed" in (result.stdout + result.stderr)
    assert not (deploy_env["root"] / "current").exists()


def test_install_release_creates_users_and_installs_generic_drop_ins(deploy_env):
    """TODO.md phase I gate (bootstrap half, MEDIUM): install-release.sh now runs setup_users
    (idempotent) and installs any *.service.d/ drop-in shipped under ops/aws/systemd/, before any
    unit is restarted -- so a live box that receives a release through jarvis-deploy without a
    bootstrap re-run still gets jarvis-ofw and the logexport drop-in."""
    result = _run_deploy(SHA_A, deploy_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"setup_users called for {SHA_A}" in deploy_env["setup_users_log"].read_text()

    systemd_dir = Path(deploy_env["env"]["JARVIS_SYSTEMD_DIR"])
    dropin_dir = systemd_dir / "jarvis-logexport@ofw.service.d"
    dropin_file = dropin_dir / "unit.conf"
    assert dropin_file.is_file()
    assert stat.S_IMODE(dropin_dir.stat().st_mode) == 0o755
    assert stat.S_IMODE(dropin_file.stat().st_mode) == 0o644


# -- Gate fix item 6: ops/aws/lib/users.sh propagates every step's failure -------------------
#
# Static, not behavioral: users.sh calls real useradd/groupadd/stat -c (GNU-only) and hardcodes
# /home/<user>/etc paths, so it can only ever run for real as root on a real Linux box (like the
# rest of ops/aws/lib). Bootstrap-engineer owns its behavioral coverage; this only proves every
# mutating step this phase touches still ends in `|| return 1`.

USERS_LIB = REPO_ROOT / "ops" / "aws" / "lib" / "users.sh"


def _extract_function(source: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n", source, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found in {USERS_LIB}"
    return match.group(1)


def _logical_lines(body: str) -> list:
    """Joins bash line-continuations (a trailing, unquoted `\\`) so a multi-line statement like
    `useradd ... \\\n  --shell ... || return 1` is checked as ONE logical line, not split across
    two where only the second half ends in `|| return 1`."""
    lines, buf = [], ""
    for raw in body.splitlines():
        buf += raw
        if buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1] + " "
            continue
        lines.append(buf)
        buf = ""
    if buf:
        lines.append(buf)
    return lines


def test_users_sh_setup_users_checks_every_mutating_step():
    body = _extract_function(USERS_LIB.read_text(encoding="utf-8"), "setup_users")
    mutating_prefixes = ("install ", "useradd ", "groupadd ", "runuser ", "chmod ", "echo root >")
    checked = 0
    for line in _logical_lines(body):
        stripped = line.strip()
        if stripped.startswith(mutating_prefixes):
            checked += 1
            assert stripped.endswith("|| return 1"), f"unchecked step in setup_users(): {stripped!r}"
    assert checked >= 10, "expected setup_users() to still contain its usual mutating steps"


def test_users_sh_create_mode_user_checks_every_mutating_step():
    body = _extract_function(USERS_LIB.read_text(encoding="utf-8"), "create_mode_user")
    mutating_prefixes = ("useradd ", "groupadd ", "chmod ", "assert_real_home_dir ")
    checked = 0
    for line in _logical_lines(body):
        stripped = line.strip()
        if stripped.startswith(mutating_prefixes):
            checked += 1
            assert stripped.endswith("|| return 1"), f"unchecked step in create_mode_user(): {stripped!r}"
    assert checked >= 3, "expected create_mode_user() to still contain its usual mutating steps"


# -- Gate fix item 5/9: scripts/release.sh secret scan over the ofw-mcp/ tree ----------------

RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release.sh"


def test_release_secret_scan_catches_planted_secrets_under_ofw_mcp(tmp_path):
    """Gate fix (item 5): the extended regex (secrets*.json, state.json, .env/.env.<suffix>)
    catches these even nested under ofw-mcp/tests/, where a careless fixture could plant one."""
    tarball = tmp_path / "jarvis-test.tar.gz"
    planted = (
        "ofw-mcp/tests/fixtures/secrets.json",
        "ofw-mcp/tests/fixtures/state.json",
        "ofw-mcp/.env.local",
    )
    with tarfile.open(tarball, "w:gz") as tf:
        for name in planted:
            data = b"{}"
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    driver = f"source '{RELEASE_SCRIPT}'\nassert_no_secrets '{tarball}'\n"
    result = subprocess.run(["bash", "-c", driver], capture_output=True, text=True, timeout=30)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    for name in planted:
        assert name in output, f"expected the scan to name {name}"


# -- Gate fix item 3: scripts/ssm-run.sh polls get-command-invocation, no fixed 100s wait ----

SSM_RUN_SCRIPT = REPO_ROOT / "scripts" / "ssm-run.sh"


def _write_ssm_run_aws_shim(bin_dir: Path, *, statuses: list, out: str = "ok", err: str = "") -> Path:
    """A fake `aws` implementing only `ssm send-command` (returns a fixed command id) and
    `ssm get-command-invocation` (pops the next value off `statuses` on every `--query Status`
    call, repeating the last one once the list is exhausted -- so a test can script "InProgress"
    N times before a terminal status)."""
    statuses_file = bin_dir / "statuses.txt"
    statuses_file.write_text("\n".join(statuses) + "\n")
    index_file = bin_dir / "status_index.txt"
    index_file.write_text("0\n")
    body = (
        'if [ "$1" = "ssm" ] && [ "$2" = "send-command" ]; then\n'
        '  echo "cmd-test-123"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "ssm" ] && [ "$2" = "get-command-invocation" ]; then\n'
        '  query=""; prev=""\n'
        '  for a in "$@"; do\n'
        '    if [ "$prev" = "--query" ]; then query="$a"; fi\n'
        '    prev="$a"\n'
        "  done\n"
        '  case "$query" in\n'
        "    Status)\n"
        f'      idx="$(cat "{index_file}")"\n'
        f'      total="$(wc -l < "{statuses_file}" | tr -d "[:space:]")"\n'
        '      [ "$idx" -lt "$total" ] || idx=$((total - 1))\n'
        f'      sed -n "$((idx + 1))p" "{statuses_file}"\n'
        f'      next=$((idx + 1)); [ "$next" -ge "$total" ] && next=$((total - 1))\n'
        f'      echo "$next" > "{index_file}"\n'
        "      ;;\n"
        f'    StandardOutputContent) printf \'%s\' "{out}" ;;\n'
        f'    StandardErrorContent) printf \'%s\' "{err}" ;;\n'
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    path = bin_dir / "aws"
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _run_ssm_run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SSM_RUN_SCRIPT), "jarvis-status", "i-fake"],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_ssm_run_polls_through_in_progress_to_success(tmp_path):
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    _write_ssm_run_aws_shim(shim_dir, statuses=["InProgress", "InProgress", "Success"], out="hello")
    env = {
        **os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "AWS_REGION": "us-east-1",
        "SSM_RUN_TIMEOUT_S": "30", "SSM_RUN_POLL_INTERVAL_S": "0.1",
    }

    result = _run_ssm_run(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "hello" in result.stdout


def test_ssm_run_reports_failure_after_polling(tmp_path):
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    _write_ssm_run_aws_shim(shim_dir, statuses=["InProgress", "Failed"], err="boom")
    env = {
        **os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "AWS_REGION": "us-east-1",
        "SSM_RUN_TIMEOUT_S": "30", "SSM_RUN_POLL_INTERVAL_S": "0.1",
    }

    result = _run_ssm_run(env)

    assert result.returncode != 0
    assert "boom" in (result.stdout + result.stderr)


def test_ssm_run_gives_up_after_its_own_timeout_when_never_terminal(tmp_path):
    """Gate fix (item 3): must not hang forever, and must not depend on botocore's fixed 100s
    `wait command-executed` budget -- bounded by SSM_RUN_TIMEOUT_S instead."""
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    _write_ssm_run_aws_shim(shim_dir, statuses=["InProgress"])
    env = {
        **os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "AWS_REGION": "us-east-1",
        "SSM_RUN_TIMEOUT_S": "1", "SSM_RUN_POLL_INTERVAL_S": "0.2",
    }

    result = _run_ssm_run(env)

    assert result.returncode != 0


def test_ssm_run_positional_timeout_wins_over_the_env_default(tmp_path):
    """The Makefile passes <timeout-seconds> as the third argument (H2); it bounds the poll even
    when SSM_RUN_TIMEOUT_S says otherwise, and is not forwarded to send-command."""
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    _write_ssm_run_aws_shim(shim_dir, statuses=["InProgress"])
    env = {
        **os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "AWS_REGION": "us-east-1",
        "SSM_RUN_TIMEOUT_S": "600", "SSM_RUN_POLL_INTERVAL_S": "0.2",
    }

    result = subprocess.run(
        ["bash", str(SSM_RUN_SCRIPT), "jarvis-status", "i-fake", "1"],
        env=env, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0
    assert "timeout 1s" in result.stderr


def test_ssm_run_no_longer_uses_the_fixed_wait_command_executed():
    content = SSM_RUN_SCRIPT.read_text(encoding="utf-8")
    # Anchored at line-start (allowing only leading whitespace) so this matches an actual
    # invocation, not this file's own explanatory comment about the fix.
    assert re.search(r"^\s*aws ssm wait\b", content, re.MULTILINE) is None


# -- scripts/sync-agents.sh (R1) --------------------------------------------------------------
#
# Kept in this file (rather than a dedicated test_sync_agents.py) because tests/test_deploy_
# script.py is the only tests/ file in deploy-engineer's ownership for this phase.
#
# AWS-StartInteractiveCommand reports only the SESSION's own exit status, which is 0 regardless
# of whether the remote `sudo runuser -- rsync` succeeded -- these tests prove sync-agents.sh's
# own "__rc=<N>" marker parsing is what actually gates success, using a fake `aws` that plays back
# a scripted session transcript instead of a real instance.

SYNC_AGENTS_SCRIPT = REPO_ROOT / "scripts" / "sync-agents.sh"


def _write_ssm_transcript_shim(bin_dir: Path, transcript_file: Path) -> None:
    """A fake `aws` implementing only `ssm start-session --document-name
    AWS-StartInteractiveCommand ...`: prints the AWS CLI's own session banner around whatever
    `transcript_file` currently holds and always exits 0, exactly matching R1's finding."""
    body = (
        'if [ "$1" = "ssm" ] && [ "$2" = "start-session" ]; then\n'
        '  echo "Starting session with SessionId: test-session-abc"\n'
        f'  cat "{transcript_file}"\n'
        '  echo "Exiting session with sessionId: test-session-abc"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    _write_shim(bin_dir, "aws", body)


@pytest.fixture
def sync_agents_env(tmp_path):
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    transcript_file = tmp_path / "transcript.txt"
    transcript_file.write_text("")

    _write_shim(shim_dir, "session-manager-plugin", "exit 0\n")
    _write_ssm_transcript_shim(shim_dir, transcript_file)

    env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "AWS_REGION": "us-east-1"}
    return {"env": env, "transcript_file": transcript_file}


def _run_sync_agents(mode: str, env: dict, stdin_text: str = "y\n") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SYNC_AGENTS_SCRIPT), mode, "i-fakeinstance"],
        input=stdin_text, env=env, capture_output=True, text=True, timeout=30,
    )


def test_sync_agents_fails_when_the_marker_is_missing(sync_agents_env):
    sync_agents_env["transcript_file"].write_text(">f.s..... CLAUDE.md\n")  # no __rc= line at all

    result = _run_sync_agents("work", sync_agents_env["env"])

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "applied" not in output
    # Pins the actual failure path (not just "some other nonzero exit"): a `grep`-on-no-match
    # exit code must not silently abort the script before this explicit message is printed.
    assert "FAIL: remote rsync failed" in output
    assert "marker 'none'" in output


def test_sync_agents_fails_when_the_marker_is_nonzero(sync_agents_env):
    sync_agents_env["transcript_file"].write_text(">f.s..... CLAUDE.md\n__rc=1\n")

    result = _run_sync_agents("work", sync_agents_env["env"])

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "applied" not in output
    assert "FAIL: remote rsync failed" in output
    assert "marker '__rc=1'" in output


def test_sync_agents_succeeds_and_applies_when_the_marker_is_zero(sync_agents_env):
    sync_agents_env["transcript_file"].write_text(">f.s..... CLAUDE.md\n__rc=0\n")

    result = _run_sync_agents("work", sync_agents_env["env"], stdin_text="y\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "applied" in (result.stdout + result.stderr)


def test_sync_agents_reports_no_changes_without_prompting_when_diff_is_empty(sync_agents_env):
    """The dry-run's own diff body is empty (only the marker line) -- sync-agents.sh must exit 0
    without ever reading stdin for a confirmation."""
    sync_agents_env["transcript_file"].write_text("__rc=0\n")

    result = subprocess.run(
        ["bash", str(SYNC_AGENTS_SCRIPT), "personal", "i-fakeinstance"],
        input="", env=sync_agents_env["env"], capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no changes" in (result.stdout + result.stderr)
