"""Client configuration: %LOCALAPPDATA%\\Jarvis\\client.yaml, created with defaults if missing.

No Windows-only imports here (pyyaml is cross-platform), so this module is fully unit-testable.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

CONFIG_FILENAME = "client.yaml"
VALID_PROFILES = ("wsl", "aws")
VALID_TOKEN_SOURCES = ("file", "secretsmanager")
DEFAULT_API_URL = "http://localhost:8765"
# AD13/G9: one daemon per mode everywhere, including wsl (127.0.0.1:8781/8782). These are the new
# per-mode urls; api_url/token_path (below) are kept only for files written before this change.
DEFAULT_API_URL_WORK = "http://localhost:8781"
DEFAULT_API_URL_PERSONAL = "http://localhost:8782"
DEFAULT_PROFILE = "wsl"          # AD16: "wsl" for a file that predates this change; install.ps1
                                  # writes "aws" explicitly for new installs.
DEFAULT_TOKEN_SOURCE = "file"    # "file" | "secretsmanager"
DEFAULT_TOKEN_SECRET_WORK = "jarvis/work/api-token"
DEFAULT_TOKEN_SECRET_PERSONAL = "jarvis/personal/api-token"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_HOTKEY_WORK = "<ctrl>+<alt>+w"
DEFAULT_HOTKEY_PERSONAL = "<ctrl>+<alt>+p"
DEFAULT_WSL_DISTRO = "Ubuntu"
DEFAULT_VAULT_WORK = "Jarvis-Work"
DEFAULT_VAULT_PERSONAL = "Jarvis-Personal"


def jarvis_dir() -> Path:
    """%LOCALAPPDATA%\\Jarvis. Falls back to a dotdir under the home directory when LOCALAPPDATA is
    unset, which is the case whenever this runs off Windows (tests, CI)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Jarvis"
    return Path.home() / ".jarvis-client"


@dataclass(frozen=True)
class ClientConfig:
    # AD16 Windows client config. `profile` picks the deployment shape; api_url/token_path are
    # legacy fields kept so a file written before this change still round-trips, but the two
    # per-mode urls/JarvisApi instances (see __main__.py) are what the client actually calls.
    profile: str = DEFAULT_PROFILE
    api_url: str = DEFAULT_API_URL
    token_path: str = ""
    api_url_work: str = DEFAULT_API_URL_WORK
    api_url_personal: str = DEFAULT_API_URL_PERSONAL
    token_source: str = DEFAULT_TOKEN_SOURCE
    aws_profile: str = ""
    token_secret_work: str = DEFAULT_TOKEN_SECRET_WORK
    token_secret_personal: str = DEFAULT_TOKEN_SECRET_PERSONAL
    whisper_exe: str = ""
    whisper_model: str = ""
    piper_exe: str | None = None
    piper_voice: str | None = None
    hotkey_work: str = DEFAULT_HOTKEY_WORK
    hotkey_personal: str = DEFAULT_HOTKEY_PERSONAL
    wsl_distro: str = DEFAULT_WSL_DISTRO
    vault_work: str = DEFAULT_VAULT_WORK
    vault_personal: str = DEFAULT_VAULT_PERSONAL
    sample_rate: int = DEFAULT_SAMPLE_RATE

    @classmethod
    def defaults(cls, base_dir: Path) -> "ClientConfig":
        return cls(
            token_path=str(base_dir / "api_token"),
            whisper_exe=str(base_dir / "whisper" / "whisper-cli.exe"),
            whisper_model=str(base_dir / "whisper" / "ggml-base.en.bin"),
        )


def config_path(base_dir: Path | None = None) -> Path:
    return (base_dir or jarvis_dir()) / CONFIG_FILENAME


def load_config(base_dir: Path | None = None) -> ClientConfig:
    """Loads client.yaml under base_dir (default: jarvis_dir()), writing it with defaults first if it
    doesn't exist yet. Unknown keys in the file are ignored; keys missing from the file fall back to
    the computed defaults."""
    base = base_dir or jarvis_dir()
    path = config_path(base)
    defaults = ClientConfig.defaults(base)
    if not path.exists():
        base.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(asdict(defaults), sort_keys=False), encoding="utf-8")
        return defaults
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_fields = asdict(defaults)
    merged = {**default_fields, **{k: v for k, v in raw.items() if k in default_fields}}
    # LOW: validate the two fields that pick a whole code path, rather than let a typo silently
    # pick the wrong one; a corrupted client.yaml falls back to the safe default instead of
    # crashing the tray app outright.
    if merged.get("profile") not in VALID_PROFILES:
        log.warning("client.yaml has invalid profile %r, falling back to %r",
                    merged.get("profile"), DEFAULT_PROFILE)
        merged["profile"] = DEFAULT_PROFILE
    if merged.get("token_source") not in VALID_TOKEN_SOURCES:
        log.warning("client.yaml has invalid token_source %r, falling back to %r",
                    merged.get("token_source"), DEFAULT_TOKEN_SOURCE)
        merged["token_source"] = DEFAULT_TOKEN_SOURCE
    return ClientConfig(**merged)
