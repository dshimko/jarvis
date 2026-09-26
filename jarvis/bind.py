"""API bind address (AD5, AD13). Exactly one address per daemon, never 0.0.0.0.

loopback  -> 127.0.0.1, allowed hosts localhost / 127.0.0.1 (local profile)
tailscale -> the first line of `tailscale ip -4`, which must be in Tailscale's CGNAT range 100.64.0.0/10;
             allowed hosts: that IP, `jarvis`, `*.ts.net`. Any failure is a startup error, never a fallback.
"""
from __future__ import annotations
import ipaddress, subprocess
from dataclasses import dataclass

LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_ALLOWED = ("127.0.0.1", "localhost")
TAILSCALE_NAMES = ("jarvis", "*.ts.net")
TAILSCALE_CMD = ("tailscale", "ip", "-4")
TAILSCALE_TIMEOUT_SECONDS = 10
DEFAULT_PORTS = {"work": 8781, "personal": 8782}
BIND_MODES = ("loopback", "tailscale")
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
MAX_PORT = 65535


@dataclass(frozen=True)
class BindSettings:
    host: str
    port: int
    allowed_hosts: tuple[str, ...]


def is_tailscale_ipv4(value: str) -> bool:
    """Tailscale CGNAT IPv4 only. RFC 1918 (the VPC address), loopback, link-local and public are refused."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.version == 4 and ip in TAILSCALE_NET


def tailscale_ip() -> str:
    try:
        res = subprocess.run(list(TAILSCALE_CMD), capture_output=True, text=True,
                             timeout=TAILSCALE_TIMEOUT_SECONDS, cwd="/")
    except (OSError, subprocess.SubprocessError) as e:
        raise SystemExit(f"Refusing to start: Tailscale is unavailable ({type(e).__name__})") from e
    lines = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]
    if res.returncode != 0 or not lines:
        raise SystemExit(f"Refusing to start: Tailscale has no IPv4 address (exit {res.returncode})")
    if not is_tailscale_ipv4(lines[0]):
        raise SystemExit(f"Refusing to start: Tailscale address {lines[0]!r} is not in 100.64.0.0/10")
    return lines[0]


def _port(api_cfg: dict, mode: str | None) -> int:
    ports = api_cfg.get("ports") or {}
    port = ports[mode] if mode in ports else api_cfg.get("port", DEFAULT_PORTS.get(mode))
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= MAX_PORT:
        raise SystemExit(f"Refusing to start: invalid api port {port!r}")
    return port


def resolve_bind(api_cfg: dict, mode: str | None = None) -> BindSettings:
    if "host" in api_cfg:
        raise SystemExit("Refusing to start: api.host is not configurable; set api.bind to loopback or tailscale")
    kind = api_cfg.get("bind", "loopback")
    if kind not in BIND_MODES:
        raise SystemExit(f"Refusing to start: api.bind {kind!r} is not one of {', '.join(BIND_MODES)}")
    port = _port(api_cfg, mode)
    if kind == "loopback":
        return BindSettings(LOOPBACK_HOST, port, LOOPBACK_ALLOWED)
    ip = tailscale_ip()
    return BindSettings(ip, port, (ip, *TAILSCALE_NAMES))


def is_safe_bind_host(host: str) -> bool:
    return host == LOOPBACK_HOST or is_tailscale_ipv4(host)
