"""Mode registry. A mode is a hard boundary: its own vault, secrets, MCP config, and tools."""
from __future__ import annotations
import logging, os, stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from dotenv import dotenv_values
from . import paths
from .config import ROOT, load_config

log = logging.getLogger(__name__)
MIN_SECRET_LEN = 8
REDACTED = "[redacted]"
MODE_PLACEHOLDER = "{mode}"

# Resolved at import from JARVIS_DEPLOYMENT / config.yaml (AD3). Shared sections (jev, outbox) come from here.
CFG = load_config()

# Env vars that must never cross into a subprocess. Everything else from os.environ is dropped too.
PASSTHROUGH = {"PATH", "HOME", "USER", "LANG", "SHELL", "TMPDIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}


def base_env() -> dict:
    """The daemon's own environ, filtered to PASSTHROUGH. No mode secrets, ever."""
    return {k: v for k, v in os.environ.items() if k in PASSTHROUGH}


@dataclass(frozen=True)
class Mode:
    name: str
    vault: Path
    env: dict
    channels: list
    daily_write_cap: int
    agents: list
    read_tools: list
    write_tools: list
    repos: dict = field(default_factory=dict)
    root: Path = ROOT
    peers: tuple = ()          # the other modes' vault paths, denied to this mode's sessions
    obsidian_vault_name: str | None = None   # Obsidian's name for the vault when the dir name differs (aws)

    @property
    def mcp_config(self) -> Path:
        """Repo copy only (WSL ext4). The vault .mcp.json is ignored (D15)."""
        return self.root / "mcp" / f"{self.name}.mcp.json"

    def subprocess_env(self) -> dict:
        """Only this mode's secrets plus a minimal base. The other mode's tokens are never present."""
        return {**base_env(), **{k: v for k, v in self.env.items() if v}, "JARVIS_MODE": self.name}


def check_env_file(path: Path) -> None:
    """B7: env files must live on the WSL filesystem and be private to the user."""
    real = path.resolve()
    if str(real).startswith("/mnt/"):
        raise SystemExit(f"Refusing to start: {path} is on the Windows filesystem ({real})")
    if real.exists() and stat.S_IMODE(real.stat().st_mode) & 0o077:
        raise SystemExit(f"Refusing to start: {path} is group/world accessible (chmod 600 it)")


def _load_env(path: Path) -> dict:
    check_env_file(path)
    if not path.exists():
        log.warning("env file %s missing; mode runs without secrets", path)
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def secret_values(envs: list[dict]) -> list[str]:
    """Every env value long enough to be a secret, longest first (so replacement is greedy)."""
    vals = {v for env in envs for v in env.values() if v and len(v) >= MIN_SECRET_LEN}
    return sorted(vals, key=len, reverse=True)


def make_redactor(modes: dict[str, Mode]) -> Callable[[str], str]:
    """C3: replace any env value of the given modes (raw or URL-encoded) with [redacted]."""
    needles = []
    for v in secret_values([m.env for m in modes.values()]):
        needles += [v] + ([quote(v, safe="")] if quote(v, safe="") != v else [])

    def redact(text):
        if not isinstance(text, str):
            return text
        for n in needles:
            text = text.replace(n, REDACTED)
        return text
    return redact


def check_mcp_config(mode: Mode, secrets: list[str]) -> None:
    """C2: the MCP config holds only ${VAR} placeholders, never a literal secret."""
    try:
        text = mode.mcp_config.read_text(encoding="utf-8")
    except OSError:
        log.warning("MCP config %s missing for mode %s", mode.mcp_config, mode.name)
        return
    if any(s in text for s in secrets):
        raise SystemExit(f"Refusing to start: {mode.mcp_config} contains a literal secret value")


def _vault_path(raw: str, name: str) -> Path:
    try:
        return Path(paths.expand(raw.replace(MODE_PLACEHOLDER, name)))
    except ValueError as e:
        log.error("Refusing to start: %s", e)
        raise SystemExit(f"Refusing to start: {e}") from e


def _env_path(raw: str, name: str, root: Path) -> Path:
    return root / os.path.expanduser(raw.replace(MODE_PLACEHOLDER, name))


def _peer_vaults(name: str, cfg: dict) -> tuple[Path, ...]:
    """The other modes' vaults from their path templates only: their env files are never touched (AD4)."""
    return tuple(_vault_path(m["vault"], other) for other, m in cfg["modes"].items() if other != name)


def load_mode(name: str, root: Path = ROOT, cfg: dict | None = None, profile: str | None = None) -> Mode:
    """Exactly one Mode. Only this mode's env file is opened; the redactor built from it holds only its secrets."""
    cfg = cfg if cfg is not None else (load_config(root, profile) if profile else CFG)
    m = (cfg.get("modes") or {}).get(name)
    if m is None:
        raise SystemExit(f"Refusing to start: unknown mode {name!r}")
    mode = Mode(
        name=name,
        vault=_vault_path(m["vault"], name),
        env=_load_env(_env_path(m["env_file"], name, root)),
        channels=m["channels"],
        daily_write_cap=m["daily_write_cap"],
        agents=m["agents"],
        read_tools=m["read_tools"],
        write_tools=m["write_tools"],
        repos={k: Path(paths.expand(v)) for k, v in (m.get("repos") or {}).items()},
        root=root,
        peers=_peer_vaults(name, cfg),
        obsidian_vault_name=m.get("obsidian_vault_name"),
    )
    check_mcp_config(mode, secret_values([mode.env]))
    return mode
