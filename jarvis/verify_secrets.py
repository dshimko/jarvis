"""Scan Windows-side dirs for any env value (C8). Prints file paths and KEY NAMES only, never values.

python -m jarvis.verify_secrets [--extra-dir DIR ...]   exit 1 if any value is found, 0 if clean.
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from dotenv import dotenv_values
from . import paths

ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = ("env/work.env", "env/personal.env")
MIN_LEN = 8
MAX_FILE_BYTES = 64 * 1024 * 1024


def load_secrets(root: Path = ROOT) -> dict[str, set[str]]:
    """value -> {KEY names} for every value of len >= MIN_LEN in either env file."""
    out: dict[str, set[str]] = {}
    for rel in ENV_FILES:
        p = root / rel
        if not p.is_file():
            continue
        for k, v in dotenv_values(p).items():
            if v and len(v) >= MIN_LEN:
                out.setdefault(v, set()).add(k)
    return out


def _needles(secrets: dict[str, set[str]]) -> list[tuple[bytes, set[str]]]:
    """utf-8 and utf-16-le forms (Windows tools often write UTF-16)."""
    return [(v.encode(enc), keys) for v, keys in secrets.items() for enc in ("utf-8", "utf-16-le")]


def _files(d: Path):
    for base, _dirs, names in os.walk(d, followlinks=False):
        for n in names:
            p = Path(base) / n
            if p.is_file() and not p.is_symlink():
                yield p


def scan(dirs: list[Path], secrets: dict[str, set[str]]) -> list[tuple[Path, list[str]]]:
    needles, hits = _needles(secrets), []
    for d in dirs:
        for p in _files(d):
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            keys = sorted({k for n, ks in needles if n in data for k in ks})
            if keys:
                hits.append((p, keys))
    return hits


def default_dirs(root: Path = ROOT) -> list[Path]:
    lad = paths.jarvis_localappdata()
    return [root / "windows_client"] + ([lad] if lad else [])


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(prog="verify_secrets")
    ap.add_argument("--extra-dir", action="append", default=[], type=Path)
    args = ap.parse_args(argv)
    secrets = load_secrets(root)
    dirs = [d for d in default_dirs(root) + args.extra_dir if d.is_dir()]
    if not secrets:
        print("No env values to check (env files empty or missing).")
        return 0
    hits = scan(dirs, secrets)
    for p, keys in hits:
        print(f"FOUND {p}: {', '.join(keys)}")
    print(f"Scanned {len(dirs)} dir(s): " + ("secrets found" if hits else "clean"))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
