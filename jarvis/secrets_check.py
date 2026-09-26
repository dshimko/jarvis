"""No secret value may be shared across modes (AD4, AD7). A pure check that reports KEY NAMES, never values.

Callers: the root `jarvis-secrets sync` tool on AWS (with the jarvis/shared keys as `shared_keys` and no
prefix exemption) and install_wsl.sh locally:

    python -m jarvis.secrets_check env/work.env env/personal.env    exit 1 on a violation, 0 if clean

The local CLI keeps the old JEV_ exemption (one Jev key may serve both modes on the workstation), and also
runs mcp_literal_violations: neither repo MCP config may hold a literal value from either env file. That
cross-mode half runs here, in a separate process, because a daemon never opens the other mode's env file.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

# Stdlib only at import: the root jarvis-secrets tool imports this with the instance's system python3.
ROOT = Path(__file__).resolve().parent.parent

LOCAL_EXEMPT_PREFIXES = ("JEV_",)
MIN_SECRET_LEN = 8                                   # same floor as modes.secret_values
MCP_FILES = ("work.mcp.json", "personal.mcp.json")


def shared_violations(work: dict, personal: dict, shared_keys: frozenset,
                      *, exempt_prefixes: tuple[str, ...] = ()) -> list[str]:
    """Sorted key names (from either side) whose non-empty value also appears in the other mode.
    Keys in `shared_keys` or starting with an exempt prefix are skipped. Values never leave this function."""
    def eligible(env: dict) -> dict:
        return {k: v for k, v in env.items()
                if v and k not in shared_keys and not k.startswith(exempt_prefixes)}
    w, p = eligible(work), eligible(personal)
    common = set(w.values()) & set(p.values())
    return sorted({k for env in (w, p) for k, v in env.items() if v in common})


def mcp_literal_violations(root: Path, work_env: dict, personal_env: dict) -> list[str]:
    """Sorted "mcp/<file>: <KEY>" for every env value (either mode, len >= MIN_SECRET_LEN) found literally
    in either repo MCP config. Values never leave this function; a missing config file is skipped."""
    secrets = [(k, v) for env in (work_env, personal_env) for k, v in env.items()
               if isinstance(v, str) and len(v) >= MIN_SECRET_LEN]
    out = set()
    for name in MCP_FILES:
        try:
            text = (root / "mcp" / name).read_text(encoding="utf-8")
        except OSError:
            continue
        out |= {f"mcp/{name}: {k}" for k, v in secrets if v in text}
    return sorted(out)


def _read_env(path: Path) -> dict:
    from dotenv import dotenv_values                  # lazy: only the CLI reads env files
    if not path.is_file():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="secrets_check", description="refuse secrets shared across modes")
    ap.add_argument("work_env", type=Path)
    ap.add_argument("personal_env", type=Path)
    args = ap.parse_args(argv)
    missing = [str(p) for p in (args.work_env, args.personal_env) if not p.is_file()]
    if missing:
        print(f"Not found (treated as empty): {', '.join(missing)}")
    work, personal = _read_env(args.work_env), _read_env(args.personal_env)
    bad = shared_violations(work, personal, frozenset(), exempt_prefixes=LOCAL_EXEMPT_PREFIXES)
    literals = mcp_literal_violations(ROOT, work, personal)
    if bad:
        print(f"Secrets shared across modes (key names only): {', '.join(bad)}")
    if literals:
        print(f"Literal secrets in MCP configs (key names only): {', '.join(literals)}")
    if bad or literals:
        return 1
    print("No secrets shared across modes and no literal secrets in MCP configs: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
