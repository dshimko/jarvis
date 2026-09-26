#!/usr/bin/env bash
# post-boot-assert: brief 9.2 / infra/DESIGN.md section 6.5. Prints one PASS/FAIL line per check
# and exits 1 if any failed. Run once at the end of bootstrap.sh and on demand via
# `jarvis-status --assert`. Root only. Reports pass/fail and counts only, never secret or vault
# content.
set -uo pipefail

failures=0
pass() { printf 'PASS: %s\n' "$1"; }
failcheck() { printf 'FAIL: %s\n' "$1"; failures=$((failures + 1)); }

# 1. Cross-home reads fail both ways. M3: first confirm, as root, that both users exist and the
# target env file is actually there -- otherwise a "read failed" result proves nothing (there
# would be nothing to find either way).
for entry in work:personal personal:work work:ofw ofw:work personal:ofw ofw:personal; do
  a="jarvis-${entry%%:*}"
  b="jarvis-${entry##*:}"
  if ! id -u "$a" >/dev/null 2>&1 || ! id -u "$b" >/dev/null 2>&1; then
    failcheck "$a or $b does not exist, cannot test cross-home isolation"
    continue
  fi
  if [ ! -f "/home/$b/.jarvis/env" ]; then
    failcheck "/home/$b/.jarvis/env does not exist (as root), cannot test cross-home isolation"
    continue
  fi
  if runuser -u "$a" -- ls "/home/$b" >/dev/null 2>&1; then
    failcheck "$a can list /home/$b"
  elif runuser -u "$a" -- cat "/home/$b/.jarvis/env" >/dev/null 2>&1; then
    failcheck "$a can read /home/$b/.jarvis/env"
  else
    pass "$a cannot list or read into /home/$b"
  fi
done

# 2. IMDS unreachable for both mode users and jarvis-ofw (must be exit 7: connection refused).
for entry in work:2001 personal:2002 ofw:2003; do
  mode="${entry%%:*}"
  user="jarvis-$mode"
  runuser -u "$user" -- curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 30' >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 7 ]; then
    pass "$user cannot reach IMDS (curl exit 7)"
  else
    failcheck "$user IMDS check returned $rc, expected 7"
  fi
done

# 3. iptables IMDS rule present.
if iptables -C OUTPUT -d 169.254.169.254/32 -m owner --uid-owner 0 -j ACCEPT 2>/dev/null && \
   iptables -C OUTPUT -d 169.254.169.254/32 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null; then
  pass "iptables IMDS rule present"
else
  failcheck "iptables IMDS rule missing"
fi

# 4. No listener on 0.0.0.0/[::]/* for the API, Syncthing, and GUI ports, and nothing on 22.
# 8783 is checked separately, below, with a stricter rule: gate finding G-ofw1, a Tailscale or
# VPC address is also a violation for a loopback-owner-gated service, not only 0.0.0.0/*/[::].
listeners=$(ss -ltnp 2>/dev/null)
for port in 8781 8782 22000 22001 8384 8385; do
  if echo "$listeners" | grep -E "(0\.0\.0\.0|\*|\[::\]):$port([^0-9]|$)" >/dev/null; then
    failcheck "port $port listens on all interfaces"
  else
    pass "port $port has no all-interfaces listener"
  fi
done
if echo "$listeners" | grep -E ':22[^0-9]|:22$' | grep -vE ':22000|:22001' >/dev/null; then
  failcheck "something listens on port 22"
else
  pass "nothing listens on port 22"
fi

# 4b. AD33 (gate finding G-ofw1): any 8783 listener not on exactly 127.0.0.1 is a violation --
# a Tailscale (100.64.0.0/10) or VPC-private address would pass the loose 0.0.0.0/*/[::] check
# above but must still fail here. Nothing listening at all is not a violation of this check (the
# separate liveness check below covers "must be listening").
port_8783_lines=$(echo "$listeners" | grep -E ':8783([^0-9]|$)' || true)
# G: `echo "" | grep -v` matches the single empty line `echo` still emits, so this must be
# guarded on non-empty output first -- otherwise "nothing listening" prints a false FAIL here
# on top of the separate, correct SKIP-FAIL from the liveness check below.
if [ -n "$port_8783_lines" ] && \
   echo "$port_8783_lines" | grep -vqE '(^|[^0-9.])127\.0\.0\.1:8783([^0-9]|$)'; then
  failcheck "port 8783 has a listener whose local address is not 127.0.0.1"
else
  pass "port 8783 has no listener bound to a non-loopback address"
fi

# 5. ssh.service masked.
if [ "$(systemctl is-enabled ssh.service 2>/dev/null)" = "masked" ]; then
  pass "ssh.service is masked"
else
  failcheck "ssh.service is not masked"
fi

# 6. EIP associated. M1: IMDSv2 token first, then assert the public IPv4 is present and
# well-formed (chose this over comparing against an expected value passed from Terraform, to
# avoid adding a new template variable cross-agent dependency for this phase).
imds_token=$(curl -fs -m 2 -X PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 30' 2>/dev/null)
pub_ip=""
if [ -n "$imds_token" ]; then
  pub_ip=$(curl -fs -m 2 -H "X-aws-ec2-metadata-token: $imds_token" \
    http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null)
fi
if [[ "$pub_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  pass "public IPv4 associated ($pub_ip)"
else
  failcheck "no valid public IPv4 visible from IMDS (IMDSv2 token present: $([ -n "$imds_token" ] && echo yes || echo no))"
fi

# 7. M2: neither mode user's process list shows the other mode's processes (AD32 proxy check).
# G3: first assert each user CAN see its own processes via the same `ps -o user= -u <user>`
# query -- otherwise an empty cross-user result would be meaningless (it could just mean ps -u
# does not work for this uid at all, not that isolation is working).
if [ -z "$(runuser -u jarvis-work -- ps -o user= -u jarvis-work 2>/dev/null)" ]; then
  failcheck "jarvis-work cannot see its own processes via ps -u (self-check failed)"
elif [ -n "$(runuser -u jarvis-work -- ps -o user= -u jarvis-personal 2>/dev/null)" ]; then
  failcheck "jarvis-work can see a jarvis-personal process"
else
  pass "jarvis-work cannot see jarvis-personal processes (self-visibility confirmed)"
fi
if [ -z "$(runuser -u jarvis-personal -- ps -o user= -u jarvis-personal 2>/dev/null)" ]; then
  failcheck "jarvis-personal cannot see its own processes via ps -u (self-check failed)"
else
  if [ -n "$(runuser -u jarvis-personal -- ps -o user= -u jarvis-work 2>/dev/null)" ]; then
    failcheck "jarvis-personal can see a jarvis-work process"
  else
    pass "jarvis-personal cannot see jarvis-work processes (self-visibility confirmed)"
  fi
  # AD33: jarvis-personal is the only user allowed to reach ofw-mcp over the network (8783), but
  # that must not extend to seeing its process list too.
  if [ -n "$(runuser -u jarvis-personal -- ps -o user= -u jarvis-ofw 2>/dev/null)" ]; then
    failcheck "jarvis-personal can see a jarvis-ofw process"
  else
    pass "jarvis-personal cannot see jarvis-ofw processes (self-visibility confirmed)"
  fi
fi
# AD33: jarvis-ofw is a third OS user, not a mode, but the same /proc hidepid isolation must
# still hold between it and jarvis-personal (the only user allowed to reach its MCP port).
if [ -z "$(runuser -u jarvis-ofw -- ps -o user= -u jarvis-ofw 2>/dev/null)" ]; then
  failcheck "jarvis-ofw cannot see its own processes via ps -u (self-check failed)"
elif [ -n "$(runuser -u jarvis-ofw -- ps -o user= -u jarvis-personal 2>/dev/null)" ]; then
  failcheck "jarvis-ofw can see a jarvis-personal process"
else
  pass "jarvis-ofw cannot see jarvis-personal processes (self-visibility confirmed)"
fi

# 8. /proc mounted with hidepid=invisible.
if findmnt -no OPTIONS /proc 2>/dev/null | grep -q 'hidepid=invisible'; then
  pass "/proc mounted with hidepid=invisible"
else
  failcheck "/proc is not mounted with hidepid=invisible"
fi

# 8b. Each mode user can resolve its own Tailscale IP unprivileged (jarvis@.service runs
# `tailscale ip -4` at startup under its own hardening; this is the same call, as the same user).
for mode in work personal; do
  user="jarvis-$mode"
  ip=$(runuser -u "$user" -- tailscale ip -4 2>/dev/null)
  if [ -n "$ip" ] && [[ "$ip" == 100.* ]]; then
    pass "$user resolves its Tailscale IP unprivileged ($ip)"
  else
    failcheck "$user could not resolve a 100.64.0.0/10 Tailscale IP (got '${ip:-empty}')"
  fi
done

# 9. APIs bound to the Tailscale IP -- only meaningful once a release has deployed jarvis@.
ts_ip=$(tailscale ip -4 2>/dev/null)
for entry in work:8781 personal:8782; do
  mode="${entry%%:*}"
  port="${entry##*:}"
  if systemctl is-active "jarvis@$mode.service" >/dev/null 2>&1; then
    if [ -n "$ts_ip" ] && echo "$listeners" | grep -q "$ts_ip:$port"; then
      pass "jarvis@$mode bound to $ts_ip:$port"
    else
      failcheck "jarvis@$mode is active but not bound to $ts_ip:$port"
    fi
  else
    pass "jarvis@$mode not active yet, bind check skipped"
  fi
done

# 10. H4: Syncthing options are child elements (<relaysEnabled>false</relaysEnabled>), not
# attributes -- grepping for relaysEnabled="false" never matches and silently always "passes".
# Parse config.xml as the owning user with python3's ElementTree instead. FAIL (not skip) when
# the unit was enabled but the config is missing.
check_syncthing_config() {
  local mode="$1" ts_ip="$2" cfg
  local user="jarvis-$mode"
  cfg="/home/$user/.local/state/syncthing/config.xml"

  if ! runuser -u "$user" -- test -f "$cfg"; then
    if systemctl is-enabled "syncthing@$user.service" >/dev/null 2>&1; then
      failcheck "$mode Syncthing is enabled but config.xml is missing"
    else
      pass "$mode Syncthing not enabled/rendered yet, config check skipped"
    fi
    return
  fi

  local result
  result=$(runuser -u "$user" -- python3 -c '
import sys
import xml.etree.ElementTree as ET

cfg, ts_ip = sys.argv[1], sys.argv[2]
try:
    root = ET.parse(cfg).getroot()
except ET.ParseError as exc:
    print(f"parse error: {exc}")
    sys.exit(0)

options = root.find("options")
if options is None:
    print("no <options> element")
    sys.exit(0)

for flag in ("relaysEnabled", "globalAnnounceEnabled", "localAnnounceEnabled",
             "natEnabled", "crashReportingEnabled"):
    el = options.find(flag)
    if el is None or (el.text or "").strip().lower() != "false":
        print(f"{flag} is not false")
        sys.exit(0)

listens = [(el.text or "").strip() for el in options.findall("listenAddress")]
if not listens:
    print("no listen addresses configured")
    sys.exit(0)
for addr in listens:
    if f"://{ts_ip}:" not in addr:
        print(f"listen address {addr} is not on the Tailscale IP")
        sys.exit(0)

gui = root.find("gui")
gui_addr_el = gui.find("address") if gui is not None else None
gui_addr = (gui_addr_el.text or "").strip() if gui_addr_el is not None else ""
if not gui_addr.startswith("127.0.0.1:"):
    print(f"GUI address {gui_addr!r} is not on 127.0.0.1")
    sys.exit(0)

print("PASS")
' "$cfg" "$ts_ip")

  if [ "$result" = "PASS" ]; then
    pass "$mode Syncthing config: relays/announce/nat/crash-reporting off, listens on tailnet IP, GUI on loopback"
  else
    failcheck "$mode Syncthing config: $result"
  fi
}

check_syncthing_config work "$ts_ip"
check_syncthing_config personal "$ts_ip"

# 11. M3: each GUI port must actually be listening on 127.0.0.1 before testing that the other
# mode user is rejected -- otherwise a GUI that never started also "passes" (nothing listening
# refuses every connection, including the legitimate one). G3: syncthing@ is Type=simple, so it
# may not be listening the instant the unit reports started; retry for up to 30s (1s steps).
for entry in work:8384 personal:8385; do
  mode="${entry%%:*}"
  own_gui="${entry##*:}"

  gui_listening=0
  waited=0
  while [ "$waited" -lt 30 ]; do
    gui_listeners=$(ss -ltnp 2>/dev/null)
    if echo "$gui_listeners" | grep -q "127.0.0.1:$own_gui"; then
      gui_listening=1
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  if [ "$gui_listening" -ne 1 ]; then
    failcheck "GUI port $own_gui is not listening on 127.0.0.1 (waited 30s)"
    continue
  fi

  other="jarvis-personal"
  [ "$mode" = "personal" ] && other="jarvis-work"
  runuser -u "$other" -- curl -s -m 2 "http://127.0.0.1:$own_gui/" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 7 ]; then
    pass "GUI port $own_gui rejects the other mode user"
  else
    failcheck "GUI port $own_gui did not reject the other mode user (rc=$rc)"
  fi
done

# 12. AD33: 8783 owner rule (root + jarvis-personal only, same shape as 8384/8385) and the 9222
# Chromium DevTools owner rule (root + jarvis-ofw only; gate I finding: without it any local uid
# could drive the logged-in browser over CDP). Both are checked with `iptables -C` for all three
# lines each -- this is what stands in for "jarvis-personal is allowed" on 8783, since ofw-mcp
# may not be running yet to actually accept a connection, and jarvis-imds-guard is untouched (it
# only ever checks the IMDS rule, not these).
if iptables -C OUTPUT -o lo -p tcp --dport 8783 -m owner --uid-owner 0 -j ACCEPT 2>/dev/null && \
   iptables -C OUTPUT -o lo -p tcp --dport 8783 -m owner --uid-owner 2002 -j ACCEPT 2>/dev/null && \
   iptables -C OUTPUT -o lo -p tcp --dport 8783 -j REJECT --reject-with tcp-reset 2>/dev/null; then
  pass "iptables 8783 owner rule present (root and jarvis-personal ACCEPT, others REJECT tcp-reset)"
else
  failcheck "iptables 8783 owner rule missing or wrong (want ACCEPT uid 0, ACCEPT uid 2002, REJECT tcp-reset)"
fi

if iptables -C OUTPUT -o lo -p tcp --dport 9222 -m owner --uid-owner 0 -j ACCEPT 2>/dev/null && \
   iptables -C OUTPUT -o lo -p tcp --dport 9222 -m owner --uid-owner 2003 -j ACCEPT 2>/dev/null && \
   iptables -C OUTPUT -o lo -p tcp --dport 9222 -j REJECT --reject-with tcp-reset 2>/dev/null; then
  pass "iptables 9222 owner rule present (root and jarvis-ofw ACCEPT, others REJECT tcp-reset)"
else
  failcheck "iptables 9222 owner rule missing or wrong (want ACCEPT uid 0, ACCEPT uid 2003, REJECT tcp-reset)"
fi

# 12b. AD33 (gate finding): a closed port and a REJECT tcp-reset both give curl exit 7, so
# "jarvis-work gets a reset" only proves anything once a 127.0.0.1:8783 listener is confirmed
# first. When nothing is listening, this must be a loud failure, never a silent skip counted as
# PASS -- ofw-mcp is expected to be running by the time this check matters.
ss_8783=$(ss -ltnp 2>/dev/null)
if echo "$ss_8783" | grep -q '127\.0\.0\.1:8783'; then
  runuser -u jarvis-personal -- curl -s -m 2 -o /dev/null "http://127.0.0.1:8783/healthz" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    pass "jarvis-personal reaches /healthz on 127.0.0.1:8783 (curl exit 0)"
  else
    failcheck "jarvis-personal could not reach /healthz on 127.0.0.1:8783 (curl exit $rc, expected 0)"
  fi

  runuser -u jarvis-work -- curl -s -m 2 -o /dev/null "http://127.0.0.1:8783/healthz" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 7 ]; then
    pass "jarvis-work is rejected on 127.0.0.1:8783 (curl exit 7)"
  else
    failcheck "jarvis-work was not rejected on 127.0.0.1:8783 (curl exit $rc, expected 7)"
  fi
else
  failcheck "SKIP-FAIL: no 127.0.0.1:8783 listener yet, cannot verify /healthz reachability"
fi

# 13. AD33: jarvis-ofw's home is exactly as isolated as a mode home (0700, self-owned).
ofw_home_info=$(stat -c '%U:%a' /home/jarvis-ofw 2>/dev/null) || ofw_home_info=""
if [ "$ofw_home_info" = "jarvis-ofw:700" ]; then
  pass "/home/jarvis-ofw is 0700 and owned by jarvis-ofw"
else
  failcheck "/home/jarvis-ofw is not 0700 owned by jarvis-ofw (got '${ofw_home_info:-missing}')"
fi

if [ "$failures" -ne 0 ]; then
  printf 'post-boot-assert: %d check(s) failed\n' "$failures"
  exit 1
fi
printf 'post-boot-assert: all checks passed\n'
