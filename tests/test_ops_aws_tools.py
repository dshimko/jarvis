"""T1 (Gate 3a fix-required): unit tests for the ops/aws/** Python tools, loaded directly from
their (extension-less) source files via importlib.SourceFileLoader since they are not part of
the jarvis/ package. Runs on macOS with the repo .venv -- no AWS CLI, systemd, or runuser needed:
every function under test here is pure or filesystem-only.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
OPS_AWS = REPO / "ops" / "aws"
JARVIS_SECRETS_PATH = OPS_AWS / "bin" / "jarvis-secrets"
LOGFILTER_PATH = OPS_AWS / "bin" / "jarvis-logfilter"
WRITE_ENV_PATH = OPS_AWS / "libexec" / "jarvis-write-env"
SYNCTHING_RENDER_PATH = OPS_AWS / "libexec" / "jarvis-syncthing-render"


def load_module(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------------------------
# jarvis-secrets: shared_violations semantics (must match jarvis.secrets_check.shared_violations
# on the same inputs), shadowed shared keys (DESIGN.md section 5 rule 2, which the app function
# does not implement), CR/LF rejection, and mcp literal detection.
# --------------------------------------------------------------------------------------------

def test_inline_shared_violations_matches_real_app_function_on_the_same_inputs():
    from jarvis.secrets_check import shared_violations as real_shared_violations

    mod = load_module("jarvis_secrets_sv", JARVIS_SECRETS_PATH)
    cases = [
        ({"A": "x", "B": "y"}, {"C": "y", "D": "z"}, frozenset()),
        ({"SHARED_ONE": "v"}, {"OTHER": "v"}, frozenset({"SHARED_ONE"})),
        ({"K": ""}, {"K": ""}, frozenset()),
        ({"A": "same", "B": "same"}, {"C": "same"}, frozenset()),
        ({"X": "1"}, {"Y": "2"}, frozenset()),
    ]
    for work, personal, shared_keys in cases:
        assert mod._inline_shared_violations(work, personal, shared_keys) == \
            real_shared_violations(work, personal, shared_keys)


def test_shadowed_shared_keys_rule_the_app_function_does_not_implement():
    mod = load_module("jarvis_secrets_shadow", JARVIS_SECRETS_PATH)
    assert mod.shadowed_shared_keys(
        {"JEV_API_KEY": "oops"}, {}, frozenset({"JEV_API_KEY"})
    ) == ["JEV_API_KEY"]
    assert mod.shadowed_shared_keys({"A": "x"}, {"B": "y"}, frozenset({"C"})) == []


def test_validate_keys_rejects_crlf_bad_format_and_non_string():
    mod = load_module("jarvis_secrets_validate", JARVIS_SECRETS_PATH)
    bad = mod.validate_keys({
        "GOOD_KEY": "clean value",
        "bad_lower": "x",
        "HAS_CR": "value\rwith cr",
        "HAS_LF": "value\nwith lf",
        "NOT_STRING": 5,
    })
    assert bad == ["HAS_CR", "HAS_LF", "NOT_STRING", "bad_lower"]


def test_inline_mcp_literal_violations_matches_real_app_function(tmp_path):
    from jarvis.secrets_check import mcp_literal_violations as real_mcp_literal_violations

    mod = load_module("jarvis_secrets_mcp", JARVIS_SECRETS_PATH)
    root = tmp_path / "approot"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "work.mcp.json").write_text('{"token": "leaked-secret-value-123"}')
    (root / "mcp" / "personal.mcp.json").write_text('{"other": "nothing-matches-here"}')

    work_env = {"SLACK_TOKEN": "leaked-secret-value-123", "SHORT": "x"}
    personal_env = {"SOME_KEY": "unrelated-value"}

    inline_result = mod._inline_mcp_literal_violations(root, work_env, personal_env)
    assert inline_result == real_mcp_literal_violations(root, work_env, personal_env)
    assert inline_result == ["mcp/work.mcp.json: SLACK_TOKEN"]


def test_mcp_literal_violations_clean_when_no_literal_secret_present(tmp_path):
    mod = load_module("jarvis_secrets_mcp_clean", JARVIS_SECRETS_PATH)
    root = tmp_path / "approot2"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "work.mcp.json").write_text('{"url": "${SLACK_MCP_URL}"}')
    (root / "mcp" / "personal.mcp.json").write_text('{"url": "${GMAIL_MCP_URL}"}')

    work_env = {"SLACK_MCP_URL_VALUE": "https://slack.example/mcp/long-enough"}
    personal_env = {"GMAIL_TOKEN": "another-long-enough-secret"}
    assert mod._inline_mcp_literal_violations(root, work_env, personal_env) == []


def test_real_repo_mcp_configs_have_no_literal_secrets_from_a_typical_env():
    """Sanity check on the actual repo mcp/ files: they hold only ${VAR} placeholders."""
    mod = load_module("jarvis_secrets_mcp_real", JARVIS_SECRETS_PATH)
    work_env = {"SLACK_WORK_TOKEN": "xoxp-work-secret-1111", "GMAIL_WORK_TOKEN": "gmail-work-secret-2222"}
    personal_env = {"GMAIL_PERSONAL_TOKEN": "gmail-personal-secret-3333", "OFW_MCP_TOKEN": "ofw-personal-secret-4444"}
    assert mod._inline_mcp_literal_violations(REPO, work_env, personal_env) == []


# --------------------------------------------------------------------------------------------
# jarvis-logfilter: forwards only JSON objects, drops and counts everything else, and recursively
# strips the AD11 content-carrying keys (case-insensitive) as a second layer.
# --------------------------------------------------------------------------------------------

def test_logfilter_forwards_json_only_and_scrubs_content_keys(monkeypatch, capsys):
    mod = load_module("jarvis_logfilter", LOGFILTER_PATH)
    lines = [
        '{"event":"heartbeat","mode":"work","body":"secret","nested":{"Text":"also secret","ts":1}}',
        "not json at all",
        "Traceback (most recent call last):",
        '{"event":"ofw_watch_error","error_class":"ValueError"}',
        "[]",  # valid JSON but not an object -- must also be dropped
    ]
    monkeypatch.setattr(sys, "argv", ["jarvis-logfilter", "work"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))

    rc = mod.main()
    assert rc == 0

    out_lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert out_lines[0] == {"event": "heartbeat", "mode": "work", "nested": {"ts": 1}}
    assert out_lines[1] == {"event": "ofw_watch_error", "error_class": "ValueError"}
    assert out_lines[2] == {"event": "nonjson_dropped", "mode": "work", "count": 3}


def test_logfilter_scrub_is_case_insensitive_and_recursive_into_lists():
    mod = load_module("jarvis_logfilter_scrub", LOGFILTER_PATH)
    obj = {"event": "x", "Body": "secret", "list": [{"TEXT": "s"}, {"keep": "yes"}]}
    scrubbed = mod.scrub(obj)
    assert scrubbed == {"event": "x", "list": [{}, {"keep": "yes"}]}


# --------------------------------------------------------------------------------------------
# jarvis-write-env: atomic O_EXCL|O_NOFOLLOW writer. A planted symlink at env.tmp must never be
# followed, and the final file must be a regular file, 0600.
# --------------------------------------------------------------------------------------------

def test_write_env_refuses_to_follow_a_symlink_and_writes_0600(tmp_path, monkeypatch):
    home = tmp_path / "home"
    jarvis_dir = home / ".jarvis"
    jarvis_dir.mkdir(parents=True)
    canary = tmp_path / "canary.txt"
    canary.write_text("do not touch")
    (jarvis_dir / "env.tmp").symlink_to(canary)

    monkeypatch.setenv("HOME", str(home))
    mod = load_module("jarvis_write_env", WRITE_ENV_PATH)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"KEY=value\n")))

    rc = mod.main()
    assert rc == 0
    assert canary.read_text() == "do not touch"  # the symlink target was never written through

    env_file = jarvis_dir / "env"
    assert env_file.is_file() and not env_file.is_symlink()
    assert env_file.read_text() == "KEY=value\n"
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"


def test_write_env_is_idempotent_on_rerun(tmp_path, monkeypatch):
    home = tmp_path / "home2"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    mod = load_module("jarvis_write_env_idem", WRITE_ENV_PATH)

    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"A=1\n")))
    assert mod.main() == 0
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"A=2\n")))
    assert mod.main() == 0
    assert (home / ".jarvis" / "env").read_text() == "A=2\n"


# --------------------------------------------------------------------------------------------
# jarvis-syncthing-render: flags, listen addresses, GUI address, exactly one folder, idempotent
# re-render, device hardening, and refusal of a non-CGNAT "Tailscale" address.
# --------------------------------------------------------------------------------------------

FAKE_CONFIG = """<configuration version="37">
    <folder id="default" label="Default Folder" path="~/Sync" type="sendreceive"></folder>
    <device id="AAAA" name="workstation" introducer="true" autoAcceptFolders="true"></device>
    <gui enabled="true" tls="false">
        <user>jarvis</user>
        <password>$2a$10$fakehash</password>
    </gui>
    <options>
        <listenAddress>default</listenAddress>
        <globalAnnounceEnabled>true</globalAnnounceEnabled>
        <relaysEnabled>true</relaysEnabled>
    </options>
</configuration>
"""


def _write_fake_config(tmp_path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    cfg_dir = home / ".local" / "state" / "syncthing"
    cfg_dir.mkdir(parents=True)
    cfg_path = cfg_dir / "config.xml"
    cfg_path.write_text(FAKE_CONFIG)
    return home, cfg_path


def test_render_sets_flags_listen_gui_one_folder_and_hardens_devices(tmp_path):
    mod = load_module("jarvis_syncthing_render", SYNCTHING_RENDER_PATH)
    home, cfg_path = _write_fake_config(tmp_path)

    mod.render(str(cfg_path), "work", str(home), "100.64.1.5")
    content = cfg_path.read_text()

    assert "<address>127.0.0.1:8384</address>" in content
    assert "tcp://100.64.1.5:22000" in content
    assert "quic://100.64.1.5:22000" in content
    for flag in ("relaysEnabled", "globalAnnounceEnabled", "localAnnounceEnabled",
                 "natEnabled", "crashReportingEnabled"):
        assert f"<{flag}>false</{flag}>" in content

    folders = re.findall(r'<folder id="([^"]+)"', content)
    assert folders == ["jarvis-work"]

    assert 'introducer="false"' in content
    assert 'autoAcceptFolders="false"' in content
    assert (home / "vault" / ".stignore").is_file()
    assert not cfg_path.with_suffix(".xml.tmp").exists()


def test_render_is_idempotent_on_rerender(tmp_path):
    mod = load_module("jarvis_syncthing_render_idem", SYNCTHING_RENDER_PATH)
    home, cfg_path = _write_fake_config(tmp_path)

    mod.render(str(cfg_path), "work", str(home), "100.64.1.5")
    mod.render(str(cfg_path), "work", str(home), "100.64.1.5")
    content = cfg_path.read_text()

    assert re.findall(r'<folder id="([^"]+)"', content) == ["jarvis-work"]
    assert content.count("<listenAddress>") == 2


def test_tailscale_ip_refuses_a_non_cgnat_address(monkeypatch):
    mod = load_module("jarvis_syncthing_render_badip", SYNCTHING_RENDER_PATH)

    class FakeCompleted:
        stdout = "203.0.113.5\n"
        returncode = 0

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeCompleted())
    with pytest.raises(SystemExit):
        mod.tailscale_ip()


def test_tailscale_ip_accepts_a_cgnat_address(monkeypatch):
    mod = load_module("jarvis_syncthing_render_goodip", SYNCTHING_RENDER_PATH)

    class FakeCompleted:
        stdout = "100.101.102.103\n"
        returncode = 0

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeCompleted())
    assert mod.tailscale_ip() == "100.101.102.103"
