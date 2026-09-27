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


# M1 (PR integration review): without this, a rebuilt/replacement instance has no
# /opt/jarvis/bin/jarvis-deploy, so run_first_deploy always skips and both daemons stay down
# until someone runs `make deploy` by hand.
def test_install_units_installs_ssm_jarvis_deploy_as_bin_jarvis_deploy():
    text = (OPS_AWS / "lib" / "install-units.sh").read_text()
    assert re.search(
        r'install\s+-m\s*0700\s+-o\s+root\s+-g\s+root\s+"\$script_dir/ssm/jarvis-deploy\.sh"'
        r'\s+/opt/jarvis/bin/jarvis-deploy\b',
        text,
    ), "install-units.sh must install ssm/jarvis-deploy.sh as /opt/jarvis/bin/jarvis-deploy (root, 0700)"


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


# --------------------------------------------------------------------------------------------
# AD33-AD41: OFW MCP server bootstrap surface (third OS user jarvis-ofw, ofw-mcp.service, the
# 8783 iptables owner rule, and the jarvis-logexport@ template/drop-in split).
# --------------------------------------------------------------------------------------------

def test_users_sh_creates_jarvis_ofw_2003_and_no_mode_dirs():
    text = (OPS_AWS / "lib" / "users.sh").read_text()
    assert 'create_mode_user "$user" 2003' in text
    assert ".local/state/ofw-mcp" in text
    assert ".cache/ms-playwright" in text
    # AD33: not a mode -- no vault, no .claude, no Syncthing state dir for jarvis-ofw.
    assert "jarvis-ofw/vault" not in text
    assert "jarvis-ofw/.claude" not in text
    assert "jarvis-ofw/.local/state/syncthing" not in text


def test_imds_guard_asserts_jarvis_ofw_uid_2003():
    text = (OPS_AWS / "bin" / "jarvis-imds-guard").read_text()
    assert "assert_user_blocked ofw 2003" in text


def test_ofw_mcp_service_shape_and_hardening():
    text = (OPS_AWS / "systemd" / "ofw-mcp.service").read_text()
    for expected in (
        "User=jarvis-ofw",
        "Group=jarvis-ofw",
        "WorkingDirectory=/opt/jarvis/current",
        "ExecStart=/opt/jarvis/current/ofw-venv/bin/ofw-mcp serve",
        "ConditionPathExists=/opt/jarvis/current/ofw-venv/bin/ofw-mcp",
        # AD40 (amended): a second, distinct-type condition so both must hold (AND), not either
        # (OR) -- see the unit file's own comment for why this isn't a second
        # ConditionPathExists= line.
        "ConditionPathExistsGlob=/home/jarvis-ofw/.jarvis/env",
        "Requires=jarvis-secrets.service jarvis-imds-guard.service",
        "Environment=OFW_MCP_PROFILE=aws",
        # Amendment 2026-09-27 (AD33): the server now binds loopback plus its own resolved
        # Tailscale IPv4, never 0.0.0.0; OFW Companion becomes a read-only consumer over that
        # listener, protected by the ACL grant plus its own bearer token.
        "Environment=OFW_MCP_BIND=tailscale:8783",
        "Environment=OFW_TZ=America/Detroit",
        "Environment=HOME=/home/jarvis-ofw",
        "Environment=PLAYWRIGHT_BROWSERS_PATH=/home/jarvis-ofw/.cache/ms-playwright",
        "BindPaths=/home/jarvis-ofw",
        "ReadWritePaths=/home/jarvis-ofw",
        "UMask=0077",
        "LimitCORE=0",
        "Restart=always",
        "RestartSec=10",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
    ):
        assert expected in text, f"missing directive: {expected!r}"
    assert re.search(r"^After=.*jarvis-secrets\.service.*jarvis-imds-guard\.service", text, re.MULTILINE)
    # Amendment 2026-09-27 (AD33): tailscaled.service joins Wants=/After=, same as
    # jarvis@.service, since the server now resolves its own Tailscale IPv4 at start.
    assert re.search(r"^Wants=.*tailscaled\.service", text, re.MULTILINE)
    assert re.search(r"^After=network-online\.target tailscaled\.service", text, re.MULTILINE)


# Every hardening/gating directive named here must be byte-for-byte identical between
# jarvis@.service (after %i -> ofw) and ofw-mcp.service -- deleting or drifting a single line in
# either unit fails this test, since ofw-mcp starts from "the jarvis@.service hardening block"
# (AD33) and is not supposed to diverge from it yet (phase D relaxes specific directives later).
_HARDENING_DIRECTIVES = (
    "NoNewPrivileges", "ProtectSystem", "ProtectHome", "BindPaths", "ReadWritePaths",
    "PrivateTmp", "PrivateDevices", "ProtectProc", "ProcSubset", "ProtectKernelTunables",
    "ProtectKernelModules", "ProtectControlGroups", "RestrictSUIDSGID", "LockPersonality",
    "RestrictRealtime", "RestrictAddressFamilies", "SystemCallArchitectures",
    "CapabilityBoundingSet", "UMask", "LimitCORE", "StartLimitIntervalSec",
)


# Directives ofw-mcp.service is expected to differ on (its own process identity/execution),
# never part of "the hardening block": anything in [Service] outside this set and outside
# _HARDENING_DIRECTIVES is unexpected and fails the test below.
_ALLOWED_EXTRA_SERVICE_KEYS = {
    "Type", "User", "Group", "WorkingDirectory", "Environment", "ExecStart",
    "ExecStartPost", "Restart", "RestartSec", "TimeoutStopSec",
}


def _ordered_hardening_lines(text: str) -> list[str]:
    """Every line (verbatim, in file order, duplicates kept) whose key is in
    _HARDENING_DIRECTIVES -- a *list*, not a dict, so an extra/reordered occurrence of a known
    hardening key is visible instead of silently collapsed to its last value."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.split("=", 1)[0] in _HARDENING_DIRECTIVES:
            out.append(stripped)
    return out


def _service_section_keys(text: str) -> set[str]:
    """Directive keys appearing anywhere in the [Service] section only (not [Unit]/[Install])."""
    keys = set()
    in_service = False
    for line in text.splitlines():
        stripped = line.strip()
        section = re.match(r"^\[(\w+)\]$", stripped)
        if section:
            in_service = section.group(1) == "Service"
            continue
        if not in_service or not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0])
    return keys


def test_ofw_mcp_hardening_matches_jarvis_at_service_after_percent_i_substitution():
    jarvis_text = (OPS_AWS / "systemd" / "jarvis@.service").read_text()
    ofw_text = (OPS_AWS / "systemd" / "ofw-mcp.service").read_text()

    # 1. The ordered sequence of hardening/gating lines must match exactly. An extra directive
    # inserted before the real one (e.g. a bogus BindPaths=/home/jarvis-personal ahead of the
    # real BindPaths=/home/jarvis-ofw) changes this list's length/order and fails here, where a
    # last-wins dict keyed by directive name would keep only the correct final value and pass.
    jarvis_lines = [line.replace("%i", "ofw") for line in _ordered_hardening_lines(jarvis_text)]
    ofw_lines = _ordered_hardening_lines(ofw_text)
    assert jarvis_lines, "jarvis@.service hardening block came back empty (fixture out of date)"
    assert ofw_lines == jarvis_lines

    # 2. No directive anywhere in ofw-mcp.service's [Service] section may fall outside both the
    # hardening set above and the small set of directives this unit is expected to differ on --
    # catches an entirely unlisted directive (AmbientCapabilities=, SupplementaryGroups=, ...)
    # that check 1 would never see, since it only looks at known hardening keys.
    unexpected = _service_section_keys(ofw_text) - set(_HARDENING_DIRECTIVES) - _ALLOWED_EXTRA_SERVICE_KEYS
    assert unexpected == set(), f"unexpected directive(s) in ofw-mcp.service [Service]: {sorted(unexpected)}"


def test_rules_v4_8783_and_9222_owner_rules_present_and_ordered():
    # Gate finding: pins both triples' lines AND their order -- both ACCEPTs must appear before
    # the REJECT, for 8783 (jarvis-personal, uid 2002) and for 9222 (jarvis-ofw, uid 2003; the
    # Chromium DevTools port `ofw-mcp login` uses, gate I finding: without this rule any local
    # uid could drive the logged-in browser over CDP).
    text = (OPS_AWS / "iptables" / "rules.v4").read_text()
    for port, uid in ((8783, 2002), (9222, 2003)):
        accept_root = f"-A OUTPUT -o lo -p tcp --dport {port} -m owner --uid-owner 0 -j ACCEPT"
        accept_user = f"-A OUTPUT -o lo -p tcp --dport {port} -m owner --uid-owner {uid} -j ACCEPT"
        reject = f"-A OUTPUT -o lo -p tcp --dport {port} -j REJECT --reject-with tcp-reset"
        assert accept_root in text, f"missing: {accept_root!r}"
        assert accept_user in text, f"missing: {accept_user!r}"
        assert reject in text, f"missing: {reject!r}"
        assert text.index(accept_root) < text.index(accept_user) < text.index(reject), (
            f"port {port}: both ACCEPTs must precede the REJECT"
        )
    # jarvis-work (2001) never gets an ACCEPT on either port; jarvis-ofw (2003) never gets one on
    # 8783; jarvis-personal (2002) never gets one on 9222.
    assert "--dport 8783 -m owner --uid-owner 2001" not in text
    assert "--dport 8783 -m owner --uid-owner 2003" not in text
    assert "--dport 9222 -m owner --uid-owner 2001" not in text
    assert "--dport 9222 -m owner --uid-owner 2002" not in text


def test_rules_v6_unchanged_since_it_has_no_8781_8782_port_rules():
    # AD33 says "mirror whatever rules.v6 does for 8781/8782" -- it does nothing for those ports
    # today (only the IMDSv6-analog rule), so neither 8783 nor 9222 belongs in rules.v6 either.
    text = (OPS_AWS / "iptables" / "rules.v6").read_text()
    assert "8783" not in text and "9222" not in text
    assert "8781" not in text and "8782" not in text


def test_logexport_template_uses_variable_and_ofw_dropin_overrides_it():
    template = (OPS_AWS / "systemd" / "jarvis-logexport@.service").read_text()
    assert "Environment=JARVIS_LOGEXPORT_UNIT=jarvis@%i.service" in template
    assert "-u ${JARVIS_LOGEXPORT_UNIT}" in template
    assert "-u jarvis@%i " not in template and "-u jarvis@%i --cursor" not in template

    dropin = (OPS_AWS / "systemd" / "jarvis-logexport@ofw.service.d" / "unit.conf").read_text()
    assert "Environment=JARVIS_LOGEXPORT_UNIT=ofw-mcp.service" in dropin
    assert "Before=ofw-mcp.service" in dropin


def test_install_units_installs_ofw_mcp_and_logexport_dropin_and_enables_them():
    install_text = (OPS_AWS / "lib" / "install-units.sh").read_text()
    assert "systemd/ofw-mcp.service" in install_text
    assert "jarvis-logexport@ofw.service.d" in install_text
    assert "ofw-mcp.service" in install_text
    assert "jarvis-logexport@ofw.service" in install_text


def test_cloudwatch_agent_ships_ofw_log_group():
    text = (OPS_AWS / "cloudwatch" / "amazon-cloudwatch-agent.json").read_text()
    assert '"/var/log/jarvis/ofw.jsonl"' in text
    assert '"/jarvis/ofw"' in text


def test_logrotate_glob_covers_ofw_jsonl():
    text = (OPS_AWS / "logrotate" / "jarvis").read_text()
    assert "/var/log/jarvis/*.jsonl" in text  # the existing glob already matches ofw.jsonl


def test_post_boot_assert_covers_ofw_checks():
    text = (OPS_AWS / "post-boot-assert.sh").read_text()
    # All 6 ordered pairs of the three users in the cross-home loop.
    assert "work:personal personal:work work:ofw ofw:work personal:ofw ofw:personal" in text
    assert "8783" in text and "9222" in text
    assert "jarvis-work is rejected on 127.0.0.1:8783" in text
    assert "jarvis-personal reaches /healthz on 127.0.0.1:8783" in text
    assert "iptables 8783 owner rule" in text
    assert "iptables 9222 owner rule" in text
    # Amendment 2026-09-27 (AD33): 8783 now legitimately listens on 127.0.0.1 AND the instance's
    # Tailscale IPv4 (OFW Companion consumer); any third address is still a violation, and once
    # ofw-mcp is active both must be present.
    assert "cannot resolve this instance's Tailscale IPv4, cannot verify port 8783 listen addresses" in text
    assert "port 8783 has a listener whose local address is neither 127.0.0.1 nor" in text
    assert "port 8783 is missing its loopback or Tailscale listener" in text
    # A skipped precondition (ofw-mcp not active yet) must never print PASS.
    assert "SKIP-FAIL: ofw-mcp not active, cannot verify 8783 dual listener" in text
    assert "ofw-mcp not active yet, 8783 dual-listener check skipped" not in text
    assert "jarvis-ofw cannot see jarvis-personal processes" in text
    assert "jarvis-personal cannot see jarvis-ofw processes" in text
    assert "/home/jarvis-ofw is 0700" in text
    assert "ofw:2003" in text  # IMDS loop


def test_jarvis_status_reports_ofw_mcp_unit_and_healthz():
    text = (OPS_AWS / "bin" / "jarvis-status").read_text()
    assert "ofw-mcp.service" in text
    assert "127.0.0.1:8783/healthz" in text
    assert ".ok" in text and ".breaker" in text


def test_jarvis_secrets_handles_ofw_target():
    text = (OPS_AWS / "bin" / "jarvis-secrets").read_text()
    assert '"ofw": "jarvis/ofw"' in text
    assert 'write_env("ofw", ofw, raw=True)' in text  # ofw-mcp reads raw KEY=value lines
    assert "shared_violations_source" in text
