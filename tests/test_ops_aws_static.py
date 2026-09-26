"""G4 (Gate 3a re-verification): plain-text regression assertions over ops/aws/** and the
user_data template. These are not behavioral tests -- they pin specific fixes made during the
Gate 3a review as literal source-text facts, so a later edit that silently reverts one of them
fails immediately instead of only showing up on a real instance.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OPS_AWS = REPO / "ops" / "aws"
TEMPLATE_PATH = REPO / "infra" / "modules" / "compute" / "templates" / "user_data.sh.tftpl"


def _iter_ops_aws_files():
    for path in OPS_AWS.rglob("*"):
        if path.is_file():
            yield path


def _section_of_line(text: str, line_no: int) -> str | None:
    """Returns the [Section] name (systemd unit-file style) that line_no (0-indexed) falls
    under, or None if it precedes any section header."""
    section = None
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"^\[(\w+)\]\s*$", line.strip())
        if m:
            section = m.group(1)
        if i == line_no:
            return section
    return None


# H3: a publish-token failure must never fail the daemon unit itself.
def test_jarvis_at_service_execstartpost_never_fails_the_daemon():
    text = (OPS_AWS / "systemd" / "jarvis@.service").read_text()
    assert "ExecStartPost=-+/opt/jarvis/bin/jarvis-secrets publish-token %i" in text


# H1: %h resolves to root's home in a system unit even with User=%i; only /home/%i is correct.
def test_syncthing_override_uses_home_percent_i_not_percent_h():
    text = (OPS_AWS / "systemd" / "syncthing-override.conf").read_text()
    assert "/home/%i" in text
    directive_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith((";", "#"))
    ]
    assert not any("%h" in line for line in directive_lines), (
        "a directive line still references %h (comments may explain the pitfall, "
        "but no actual directive should use it)"
    )


# H3 / systemd correctness: StartLimitIntervalSec is only valid under [Unit].
def test_start_limit_interval_sec_only_appears_under_unit_sections():
    checked = 0
    for path in (OPS_AWS / "systemd").iterdir():
        if not path.is_file():
            continue
        text = path.read_text()
        for i, line in enumerate(text.splitlines()):
            if "StartLimitIntervalSec" in line:
                checked += 1
                assert _section_of_line(text, i) == "Unit", (
                    f"{path.name}: StartLimitIntervalSec outside [Unit] at line {i + 1}: {line!r}"
                )
    assert checked >= 2, "expected StartLimitIntervalSec in at least jarvis@.service and the Syncthing drop-in"


# H2: the guard must not run before the network is up on a fresh boot.
def test_imds_guard_service_waits_for_network_online():
    text = (OPS_AWS / "systemd" / "jarvis-imds-guard.service").read_text()
    assert "Wants=network-online.target" in text
    assert re.search(r"^After=.*network-online\.target", text, re.MULTILINE)


# C1: root must never create or chown a path under a mode home directly; every such write must
# be routed through `runuser -u <user> --`.
def test_users_sh_creates_mode_home_subdirs_only_via_runuser():
    text = (OPS_AWS / "lib" / "users.sh").read_text()
    offending = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.search(r"/home/\$(user|name)/\S", stripped):
            continue
        if re.search(r"\binstall -d\b|\bmkdir\b|\bchown\b", stripped) and not stripped.startswith("runuser"):
            offending.append(stripped)
    assert offending == [], f"users.sh writes under a mode home without runuser: {offending}"


# H5: jarvis-vault-commit must initialize its own repo lazily; nothing else runs `git init`.
def test_vault_commit_initializes_git_repo_lazily():
    text = (OPS_AWS / "libexec" / "jarvis-vault-commit").read_text()
    assert "git init" in text


# G1: gpg only auto-creates a homedir literally named ".gnupg"; both ephemeral keyrings must be
# created explicitly before any --import.
def test_template_creates_both_gnupg_homedirs_before_use():
    text = TEMPLATE_PATH.read_text()
    assert 'install -d -m 0700 "$tmp_awscli/gnupg"' in text
    assert 'install -d -m 0700 "$tmp_cwa/gnupg"' in text
    for marker in ('"$tmp_awscli/gnupg"', '"$tmp_cwa/gnupg"'):
        create_idx = text.index(f"install -d -m 0700 {marker}")
        import_idx = text.index(f'--homedir {marker} --import', create_idx)
        assert import_idx > create_idx, f"{marker}: --import appears before its homedir is created"


# L3: a plain `netfilter-persistent reload` would flush tailscaled's own dynamically-inserted
# chains; every restore call must be --noflush.
def test_every_iptables_restore_call_uses_noflush():
    hits = []
    for path in _iter_ops_aws_files():
        try:
            text = path.read_text()
        except (UnicodeDecodeError, PermissionError):
            continue
        for line in text.splitlines():
            if "iptables-restore" in line or "ip6tables-restore" in line:
                hits.append((path, line.strip()))
    assert hits, "expected at least one iptables-restore/ip6tables-restore call under ops/aws/"
    for path, line in hits:
        assert "--noflush" in line, f"{path}: missing --noflush: {line!r}"


# DESIGN.md section 5: the join must never accept SSH or fall back to MagicDNS resolution.
def test_tailscale_join_has_accept_dns_false_and_ssh_false():
    text = (OPS_AWS / "lib" / "tailscale-join.sh").read_text()
    assert "--accept-dns=false" in text
    assert "--ssh=false" in text


# Sanity: no leftover Go-template/Handlebars-style double-brace syntax anywhere under ops/aws/
# (this is a plain bash+systemd tree, not Go templates; a stray {{ would indicate a copy-paste
# artifact from an unrelated templating system).
def test_no_stray_double_brace_under_ops_aws():
    hits = [str(p) for p in _iter_ops_aws_files() if "{{" in _safe_read(p)]
    assert hits == [], f"stray '{{{{' found in: {hits}"


def _safe_read(path: Path) -> str:
    try:
        return path.read_text()
    except (UnicodeDecodeError, PermissionError):
        return ""
