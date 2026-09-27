"""PLAN.md AD34 / RUNBOOK.md section 3.1: scripts/ofw-companion-token.sh generates or rotates
OFW Companion's read-only ofw-mcp bearer token end to end -- merging its sha256 into the
jarvis/ofw secret and handing the raw value to Companion via the macOS Keychain entries
ofw-auth.ts itself uses -- without the raw token, its full sha256, or any other secret value ever
appearing in stdout, stderr, or on disk once the script exits.

Gate fix (2026-09-27, round 1): JarvisOperator cannot GetSecretValue on value secrets at all
(infra/org/policies.tf, "PutValueSecrets" -- Put/DescribeSecret only), so the get-secret-value
call's stderr is never discarded: a ResourceNotFoundException (or a success with an empty/None
SecretString) is the only shape that means "no version yet"; every other failure (AccessDenied,
an expired SSO token, ...) is classified by its bracketed exception name (or the fixed fallback
"CLIError" when there is none) and printed as a bounded `FAIL: <op> failed: <class>` line, never
the stderr's full text.

Gate fix (2026-09-27, round 2): the Keychain write no longer uses a here-document. On bash before
5.1 (macOS's `/bin/bash` 3.2 included) a here-document/here-string is backed by a real, if
briefly-lived, 0600 temp file under $TMPDIR -- so `security -i` fed that way would put the token
on disk after all. It is now fed by a `printf` builtin piped straight into `security -i`'s stdin,
with no quoting around any value (OFW_MCP_URL is validated first: whitespace, a newline, or a
quote character is refused before any AWS call or Keychain write). `security -i`'s own exit
status is not trusted as a failure signal either; a `security find-generic-password` lookup
(never `-w`, so it prints nothing) verifies the write afterward and is what the fixed "re-run"
message is actually gated on.

Runs the real script under PATH shims for `aws` (records every invocation; serves a fixture
jarvis/ofw JSON, or simulates a failure by cating a fixture stderr file and exiting non-zero for
either secretsmanager operation), `security` (records every invocation -- in `-i` mode, its stdin
lines -- instead of touching the real macOS Keychain; `-i` and `find-generic-password` can each be
made to fail independently), and `make` (records every invocation -- the script must never call
it, see the script's own header comment for why driving ofw-auth.ts non-interactively would
hang). `openssl` and `python3` are the real binaries. Skipped on Windows: the script is bash-only.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="ofw-companion-token.sh is Linux/macOS bash-only"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ofw-companion-token.sh"

FIXTURE_SECRET = {
    "OFW_USERNAME": "coparent-relay",
    "OFW_PASSWORD": "correct-horse-battery-staple",
    "OFW_MCP_TOKEN_SHA256": "a" * 64,
    "OFW_MCP_WRITE_TOKEN_SHA256": "b" * 64,
    "OFW_RECIPIENTS": "coparent=12345",
}

HASH_KEY = "OFW_MCP_COMPANION_TOKEN_SHA256"
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

RESOURCE_NOT_FOUND_STDERR = (
    "An error occurred (ResourceNotFoundException) when calling the GetSecretValue operation: "
    "Secrets Manager can't find the specified secret value for VersionId: AWSCURRENT"
)
ACCESS_DENIED_STDERR = (
    "An error occurred (AccessDeniedException) when calling the GetSecretValue operation: "
    "User: arn:aws:sts::123456789012:assumed-role/JarvisOperator/session is not authorized to "
    "perform: secretsmanager:GetSecretValue on resource: jarvis/ofw"
)
ACCESS_DENIED_PUT_STDERR = (
    "An error occurred (AccessDeniedException) when calling the PutSecretValue operation: "
    "User: arn:aws:sts::123456789012:assumed-role/JarvisOperator/session is not authorized to "
    "perform: secretsmanager:PutSecretValue on resource: jarvis/ofw"
)
EXPIRED_SSO_STDERR = (
    "The SSO session associated with this profile has expired or is otherwise invalid. To "
    "refresh this SSO session run aws sso login with the corresponding profile."
)


def _write_shim(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


_AWS_SHIM_BODY = """
set -euo pipefail
printf '%s\\n' "$*" >> "$AWS_CALL_LOG"

if [ "${1:-}" = "secretsmanager" ] && [ "${2:-}" = "get-secret-value" ]; then
  if [ -n "${AWS_GET_FAIL_STDERR_FILE:-}" ] && [ -f "${AWS_GET_FAIL_STDERR_FILE:-}" ]; then
    cat "$AWS_GET_FAIL_STDERR_FILE" >&2
    exit 1
  fi
  if [ -n "${AWS_EMPTY_MARKER:-}" ] && [ -f "${AWS_EMPTY_MARKER:-}" ]; then
    exit 0
  fi
  cat "$AWS_FIXTURE_FILE"
  exit 0
fi

if [ "${1:-}" = "secretsmanager" ] && [ "${2:-}" = "put-secret-value" ]; then
  if [ -n "${AWS_PUT_FAIL_STDERR_FILE:-}" ] && [ -f "${AWS_PUT_FAIL_STDERR_FILE:-}" ]; then
    cat "$AWS_PUT_FAIL_STDERR_FILE" >&2
    exit 1
  fi
  prev=""
  for arg in "$@"; do
    if [ "$prev" = "--secret-string" ]; then
      path="${arg#file://}"
      cp "$path" "$AWS_PUT_CAPTURE_FILE"
    fi
    prev="$arg"
  done
  echo '{"Name":"jarvis/ofw","VersionId":"v2"}'
  exit 0
fi

echo "unhandled aws invocation: $*" >&2
exit 1
"""

# -i: appends its stdin (the `printf`-piped command lines) to the log; can be made to fail via
# SECURITY_FAIL_MARKER (independent of find-generic-password below, matching the script's own
# refusal to trust `security -i`'s exit status).
# find-generic-password: the verification lookup the script actually gates its fixed "re-run"
# message on; controlled independently via SECURITY_FIND_FAIL_MARKER.
_SECURITY_SHIM_BODY = """
if [ "${1:-}" = "-i" ]; then
  cat >> "$SECURITY_LOG"
  if [ -n "${SECURITY_FAIL_MARKER:-}" ] && [ -f "${SECURITY_FAIL_MARKER:-}" ]; then
    echo "simulated security -i failure" >&2
    exit 1
  fi
  exit 0
fi

if [ "${1:-}" = "find-generic-password" ]; then
  printf '%s\\n' "$*" >> "$SECURITY_LOG"
  if [ -n "${SECURITY_FIND_FAIL_MARKER:-}" ] && [ -f "${SECURITY_FIND_FAIL_MARKER:-}" ]; then
    exit 44
  fi
  exit 0
fi

printf '%s\\n' "$*" >> "$SECURITY_LOG"
exit 0
"""


@pytest.fixture
def ofw_env(tmp_path):
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    tmpdir = tmp_path / "scratch-tmp"
    tmpdir.mkdir()

    aws_call_log = tmp_path / "aws_calls.log"
    aws_fixture_file = tmp_path / "fixture_secret.json"
    aws_fixture_file.write_text(json.dumps(FIXTURE_SECRET))
    aws_put_capture_file = tmp_path / "put_capture.json"
    aws_get_fail_stderr_file = tmp_path / "aws_get_fail_stderr"  # absent by default
    aws_put_fail_stderr_file = tmp_path / "aws_put_fail_stderr"  # absent by default
    aws_empty_marker = tmp_path / "aws_empty_marker"  # absent by default

    security_log = tmp_path / "security_calls.log"
    security_fail_marker = tmp_path / "security_fail_marker"  # absent by default
    security_find_fail_marker = tmp_path / "security_find_fail_marker"  # absent by default
    make_log = tmp_path / "make_calls.log"

    _write_shim(shim_dir, "aws", _AWS_SHIM_BODY)
    _write_shim(shim_dir, "security", _SECURITY_SHIM_BODY)
    _write_shim(shim_dir, "make", f'printf "%s\\n" "$*" >> "{make_log}"\nexit 0\n')

    env = {
        **os.environ,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "TMPDIR": str(tmpdir),
        "COMPANION_DIR": str(tmp_path / "divorceCommunications"),
        "AWS_REGION": "us-east-1",
        "AWS_PROFILE": "jarvis-prod-test",
        "AWS_CALL_LOG": str(aws_call_log),
        "AWS_FIXTURE_FILE": str(aws_fixture_file),
        "AWS_PUT_CAPTURE_FILE": str(aws_put_capture_file),
        "AWS_GET_FAIL_STDERR_FILE": str(aws_get_fail_stderr_file),
        "AWS_PUT_FAIL_STDERR_FILE": str(aws_put_fail_stderr_file),
        "AWS_EMPTY_MARKER": str(aws_empty_marker),
        "SECURITY_LOG": str(security_log),
        "SECURITY_FAIL_MARKER": str(security_fail_marker),
        "SECURITY_FIND_FAIL_MARKER": str(security_find_fail_marker),
    }
    return {
        "env": env,
        "tmpdir": tmpdir,
        "aws_call_log": aws_call_log,
        "aws_fixture_file": aws_fixture_file,
        "aws_put_capture_file": aws_put_capture_file,
        "aws_get_fail_stderr_file": aws_get_fail_stderr_file,
        "aws_put_fail_stderr_file": aws_put_fail_stderr_file,
        "aws_empty_marker": aws_empty_marker,
        "security_log": security_log,
        "security_fail_marker": security_fail_marker,
        "security_find_fail_marker": security_find_fail_marker,
        "make_log": make_log,
    }


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)


_ADD_GENERIC_PASSWORD_RE = re.compile(r"-a (\S+) -w (\S+)")


def _extract_security_value(security_log_text: str, account: str) -> str:
    for line in security_log_text.splitlines():
        match = _ADD_GENERIC_PASSWORD_RE.search(line)
        if match and match.group(1) == account:
            return match.group(2)
    raise AssertionError(f"no 'add-generic-password ... -a {account} -w ...' line found in log:\n{security_log_text}")


def _extract_security_token(security_log_text: str) -> str:
    return _extract_security_value(security_log_text, "token")


def _extract_security_url(security_log_text: str) -> str:
    return _extract_security_value(security_log_text, "url")


def _no_put_happened(aws_call_log: Path) -> bool:
    if not aws_call_log.exists():
        return True
    return "put-secret-value" not in aws_call_log.read_text()


def _security_never_called(security_log: Path) -> bool:
    return not security_log.exists() or security_log.read_text() == ""


# -- happy path ---------------------------------------------------------------------------------


def test_happy_path_exits_zero_and_merges_json(ofw_env):
    result = _run(ofw_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    merged = json.loads(ofw_env["aws_put_capture_file"].read_text())
    for key, value in FIXTURE_SECRET.items():
        assert merged[key] == value, f"existing key {key} was not preserved"
    assert HASH_KEY in merged
    assert SHA256_HEX_RE.match(merged[HASH_KEY]), merged[HASH_KEY]


def test_put_secret_string_uses_file_url(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    log = ofw_env["aws_call_log"].read_text()
    put_lines = [line for line in log.splitlines() if "put-secret-value" in line]
    assert len(put_lines) == 1, log
    assert "--secret-string file://" in put_lines[0]


def test_region_present_on_every_aws_call(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    log = ofw_env["aws_call_log"].read_text()
    lines = [line for line in log.splitlines() if line.strip()]
    assert len(lines) >= 2, log
    for line in lines:
        assert "--region us-east-1" in line, line


def test_temp_scratch_dir_removed_after_run(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    leftover = list(ofw_env["tmpdir"].glob("jarvis-ofw-companion-token.*"))
    assert leftover == [], f"scratch dir(s) not cleaned up: {leftover}"


def test_make_is_never_invoked(ofw_env):
    """The script must never try to drive Companion's own `make ofw-auth` (see the script's
    header comment on why piping to ofw-auth.ts's hidden-token prompt hangs)."""
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert not ofw_env["make_log"].exists() or ofw_env["make_log"].read_text() == ""


def test_printed_lines_are_fixed_and_do_not_include_env_values(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    assert "updated secret jarvis/ofw" in result.stdout
    assert "stored Companion token" in result.stdout
    assert "token hash starts with" in result.stdout
    assert "now run: make secrets-sync" in result.stdout


def test_cross_check_hint_names_the_profile_in_use(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AWS_PROFILE=jarvis-prod-test" in result.stdout


def test_rotation_order_line_is_exact(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "now run: make secrets-sync (the box keeps the old hash until then; "
        "Companion gets 401 meanwhile)" in result.stdout
    )


# -- gate round 2: no here-document, no quoting, validated URL, verified write --------------------


def test_script_has_no_here_document():
    assert "<<" not in SCRIPT.read_text(encoding="utf-8")


def test_companion_keychain_entries_written_via_stdin_not_argv(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    log = ofw_env["security_log"].read_text()
    assert "ofw-companion-ofw-mcp" in log
    url = _extract_security_url(log)
    assert url == "http://jarvis:8783/mcp"
    token = _extract_security_token(log)
    assert re.fullmatch(r"[0-9a-f]{64}", token), token
    # -U (update in place) matches ofw-auth.ts's own re-run behavior. The shim only appends
    # these lines to the log from its stdin ("-i" branch), so their presence here already proves
    # `security -i` (not plain argv) was used to deliver the token.
    assert "-U -s ofw-companion-ofw-mcp" in log


def test_keychain_stream_has_no_quoting(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    log = ofw_env["security_log"].read_text()
    add_lines = [line for line in log.splitlines() if line.startswith("add-generic-password")]
    assert len(add_lines) == 2, log
    for line in add_lines:
        assert '"' not in line, line
        assert "'" not in line, line


def test_verify_lookup_happens_after_the_write(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    log = ofw_env["security_log"].read_text()
    token_write_index = log.index(f'-a token -w {_extract_security_token(log)}')
    verify_index = log.index("find-generic-password")
    assert token_write_index < verify_index, log


def test_verify_lookup_never_passes_dash_w(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr

    log = ofw_env["security_log"].read_text()
    verify_lines = [line for line in log.splitlines() if line.startswith("find-generic-password")]
    assert len(verify_lines) == 1, log
    assert "-w" not in verify_lines[0], verify_lines[0]


def test_security_i_failure_alone_does_not_fail_the_script(ofw_env):
    """security -i's own exit status is not trusted (gate round 2): only the
    find-generic-password verification decides success/failure."""
    ofw_env["security_fail_marker"].write_text("1")

    result = _run(ofw_env["env"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "stored Companion token" in result.stdout


def test_security_find_failure_reports_fixed_message_after_secret_was_updated(ofw_env):
    ofw_env["security_find_fail_marker"].write_text("1")

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "secret updated but the Keychain write failed; re-run make ofw-companion-token" in combined
    # The secret WAS updated before the Keychain write was attempted/verified.
    assert "updated secret jarvis/ofw" in result.stdout
    merged = json.loads(ofw_env["aws_put_capture_file"].read_text())
    assert HASH_KEY in merged


def test_ofw_mcp_url_with_embedded_space_is_refused_before_any_write(ofw_env):
    env = {**ofw_env["env"], "OFW_MCP_URL": "http://jarvis:8783/mcp evil"}

    result = _run(env)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "OFW_MCP_URL must not contain whitespace" in combined
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


def test_ofw_mcp_url_with_embedded_newline_is_refused_before_any_write(ofw_env):
    env = {**ofw_env["env"], "OFW_MCP_URL": "http://jarvis:8783/mcp\nrm -rf /"}

    result = _run(env)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "OFW_MCP_URL must not contain whitespace" in combined
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


def test_ofw_mcp_url_with_quote_is_refused_before_any_write(ofw_env):
    env = {**ofw_env["env"], "OFW_MCP_URL": 'http://jarvis:8783/mcp"'}

    result = _run(env)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "OFW_MCP_URL must not contain" in combined
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


# -- secret exposure ------------------------------------------------------------------------------


def test_stdout_and_stderr_never_contain_token_or_full_hash_or_other_secrets(ofw_env):
    result = _run(ofw_env["env"])
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr

    token = _extract_security_token(ofw_env["security_log"].read_text())
    assert token not in combined

    merged = json.loads(ofw_env["aws_put_capture_file"].read_text())
    full_hash = merged[HASH_KEY]
    assert full_hash not in combined
    assert full_hash[:8] in result.stdout  # the one fixed cross-check line

    for key, value in FIXTURE_SECRET.items():
        assert value not in combined, f"leaked value of {key}"


# -- refusal: no secret version yet ---------------------------------------------------------------


def test_no_version_yet_refuses_before_any_put(ofw_env):
    ofw_env["aws_get_fail_stderr_file"].write_text(RESOURCE_NOT_FOUND_STDERR)

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    assert "no secret version yet" in (result.stdout + result.stderr)
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])
    leftover = list(ofw_env["tmpdir"].glob("jarvis-ofw-companion-token.*"))
    assert leftover == []


def test_empty_secret_string_refuses_before_any_put(ofw_env):
    ofw_env["aws_empty_marker"].write_text("1")

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    assert "no secret version yet" in (result.stdout + result.stderr)
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


def test_no_version_yet_never_leaks_generated_token(ofw_env):
    """Even on the refusal path, the token already generated in-process before the secret read
    must never reach stdout/stderr."""
    ofw_env["aws_get_fail_stderr_file"].write_text(RESOURCE_NOT_FOUND_STDERR)

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    assert _security_never_called(ofw_env["security_log"])


# -- refusal: any other get/put failure is classified, never dumped verbatim ----------------------


def test_access_denied_on_get_prints_class_only_message_and_no_writes(ofw_env):
    ofw_env["aws_get_fail_stderr_file"].write_text(ACCESS_DENIED_STDERR)

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "get-secret-value failed: AccessDeniedException" in combined
    assert "no secret version yet" not in combined
    assert "JarvisOperator" not in combined  # the fixture's principal detail never leaks
    assert "assumed-role" not in combined
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


def test_expired_sso_on_get_prints_cli_error_fallback_and_no_writes(ofw_env):
    ofw_env["aws_get_fail_stderr_file"].write_text(EXPIRED_SSO_STDERR)

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "get-secret-value failed: CLIError" in combined
    assert "no secret version yet" not in combined
    assert "SSO session" not in combined  # bounded message only, never the CLI's own text
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


def test_put_failure_prints_class_only_message_and_never_touches_companion(ofw_env):
    ofw_env["aws_put_fail_stderr_file"].write_text(ACCESS_DENIED_PUT_STDERR)

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "put-secret-value failed: AccessDeniedException" in combined
    assert "updated secret jarvis/ofw" not in combined
    assert "assumed-role" not in combined
    assert _security_never_called(ofw_env["security_log"])
    log = ofw_env["aws_call_log"].read_text()
    assert "get-secret-value" in log
    assert "put-secret-value" in log  # the attempt happened; it just failed


# -- refusal: malformed jarvis/ofw secret content --------------------------------------------------


def test_malformed_json_secret_refuses_before_any_write(ofw_env):
    ofw_env["aws_fixture_file"].write_text('{"a": bad')

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "not valid JSON" in combined
    assert "Traceback" not in combined
    assert "bad" not in combined
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


def test_json_array_secret_refuses_before_any_write(ofw_env):
    ofw_env["aws_fixture_file"].write_text("[1, 2, 3]")

    result = _run(ofw_env["env"])

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "not valid JSON" in combined
    assert "Traceback" not in combined
    assert _no_put_happened(ofw_env["aws_call_log"])
    assert _security_never_called(ofw_env["security_log"])


def test_script_has_no_double_curly_braces():
    assert "{{" not in SCRIPT.read_text(encoding="utf-8")
