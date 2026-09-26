"""Deployment profiles (AD3): config.<profile>.yaml deep-merged over config.yaml.

The profile comes from JARVIS_DEPLOYMENT (systemd on AWS sets `aws`), else `deployment:` in config.yaml,
else `local`. A missing profile file is a startup error, never a silent fallback to the base config.
"""
from __future__ import annotations
import os, re
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASE_FILE = "config.yaml"
DEFAULT_PROFILE = "local"
ENV_VAR = "JARVIS_DEPLOYMENT"
PROFILE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")      # also keeps the name from escaping ROOT


def _read(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Refusing to start: {path.name} is not a mapping")
    return data


def resolve_deployment(base: dict | None = None) -> str:
    """JARVIS_DEPLOYMENT wins, then `deployment:` in the base config, then `local`."""
    raw = os.environ.get(ENV_VAR)
    if raw is None:
        raw = (base or {}).get("deployment") or DEFAULT_PROFILE
    if not isinstance(raw, str) or not PROFILE_RE.match(raw):
        raise SystemExit(f"Refusing to start: invalid deployment profile {raw!r}")
    return raw


def deep_merge(base: dict, over: dict) -> dict:
    """A new dict: mappings merge recursively, everything else (lists included) is replaced by `over`."""
    out = {k: (deep_merge(v, {}) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = deep_merge(v, {}) if isinstance(v, dict) else v
    return out


def load_config(root: Path = ROOT, profile: str | None = None) -> dict:
    base = _read(root / BASE_FILE)
    name = profile if profile is not None else resolve_deployment(base)
    if not PROFILE_RE.match(name):
        raise SystemExit(f"Refusing to start: invalid deployment profile {name!r}")
    path = root / f"config.{name}.yaml"
    if not path.is_file():
        raise SystemExit(f"Refusing to start: deployment profile file {path.name} is missing")
    return {**deep_merge(base, _read(path)), "deployment": name}
