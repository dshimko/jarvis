"""Client configuration: %LOCALAPPDATA%\\Jarvis\\client.yaml, created with defaults if missing.

No Windows-only imports here (pyyaml is cross-platform), so this module is fully unit-testable.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

CONFIG_FILENAME = "client.yaml"
DEFAULT_API_URL = "http://localhost:8765"
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
    api_url: str = DEFAULT_API_URL
    token_path: str = ""
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
    return ClientConfig(**merged)
