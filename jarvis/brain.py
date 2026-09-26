"""Claude Code headless calls, scoped to one mode's vault, MCP config, env, and read-only tool set.

Permission rules, verified against the real CLI (tests/test_claude_cli.py):
  - Read(//abs/**) in --disallowedTools also blocks Grep and Glob on that path; Grep(path) / Glob(path)
    specifiers are ignored, so path denies are written as Read(...).
  - Edit(//abs/**) also blocks Write; a Write(path) deny is ignored, so write denies are Edit(...).
  - Denies match the resolved path (symlinks and /tmp -> /private/tmp aliases are caught), but both the
    configured path and its realpath are listed anyway.
  - /proc, /run/user and /dev/shm are denied too; on macOS they don't exist and the rules are inert.
  - Read-only tools need no approval, so allow-scoping alone does not confine reads: the deny list must.
"""
from __future__ import annotations
import json, logging, os, subprocess, datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from . import paths
from .logsetup import log_event
from .modes import Mode

log = logging.getLogger(__name__)
NO_HOOKS = json.dumps({"disableAllHooks": True})
_REDACTOR: list[Callable[[str], str]] = []


def set_redactor(fn: Callable[[str], str] | None) -> None:
    """main wires the all-modes redactor; without it, stderr excerpts are not redacted (identity default)."""
    _REDACTOR.clear()
    if fn:
        _REDACTOR.append(fn)


def _redact(text: str) -> str:
    return (_REDACTOR[0] if _REDACTOR else (lambda t: t))(text)
SETTINGS_FILES = ("settings.json", "settings.local.json")
# Settings keys that can run commands or widen permissions. Any of them in a vault settings file refuses the run.
# /proc/<pid>/environ and cmdline expose the daemon's env; /run/user and /dev/shm hold sockets and shared memory.
SYSTEM_TREES = (Path("/proc"), Path("/run/user"), Path("/dev/shm"))
# Own-vault paths the agent must not write: Claude config, MCP config, Obsidian plugins/config, git config/hooks.
OWN_WRITE_DENY_TREES = (".claude", ".obsidian", ".git")
GIT_RISKY = ("fsmonitor", "hookspath")
# OFW write-token control tools (AD38/AD39): code-handled in ofw_control only. Denied in every session as
# defense in depth (the read token cannot call them either); never write_tools, so no outbox item can name them.
CONTROL_TOOLS = ("mcp__ofw__confirm_privileged", "mcp__ofw__reset_breaker")
RISKY_SETTINGS = {"hooks", "permissions", "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport",
                  "otelHeadersHelper", "statusLine", "env", "enableAllProjectMcpServers", "enabledMcpjsonServers"}


def _abs(p: Path | str) -> str:
    """Absolute path in Claude's //abs/path rule syntax."""
    return "/" + os.path.abspath(os.path.expanduser(str(p)))


def _forms(p: Path) -> list[Path]:
    """The configured path and its realpath (deduplicated)."""
    out = [Path(os.path.abspath(os.path.expanduser(str(p))))]
    real = Path(os.path.realpath(out[0]))
    return out + ([real] if real != out[0] else [])


def _deny_tree(p: Path, tools=("Read", "Edit")) -> list[str]:
    return [f"{t}({_abs(f)}{suffix})" for f in _forms(p) for t in tools for suffix in ("", "/**")]


def _deny_file(p: Path, tools=("Read", "Edit")) -> list[str]:
    return [f"{t}({_abs(f)})" for f in _forms(p) for t in tools]


def secret_denies(mode: Mode) -> list[str]:
    """D14: paths no vault session may read or write, whatever its allow list says."""
    home = Path.home()
    rules = []
    for peer in mode.peers:
        rules += _deny_tree(peer)
    for tree in (mode.root, home / ".claude", home / ".jarvis", home / ".ssh") + SYSTEM_TREES:
        rules += _deny_tree(tree)
    rules += _deny_file(home / ".claude.json")
    lad = paths.jarvis_localappdata()
    if lad is not None:
        rules += _deny_tree(lad)
    return rules


def tool_policy(mode: Mode) -> tuple[list[str], list[str]]:
    """(allowed, disallowed) for a vault session. Deny wins over allow."""
    allowed = [f"Read({_abs(mode.vault)}/**)", f"Edit({_abs(mode.vault)}/**)", "Glob", "Grep"] + list(mode.read_tools)
    own_config = [r for d in OWN_WRITE_DENY_TREES for r in _deny_tree(mode.vault / d, tools=("Edit",))] \
        + _deny_file(mode.vault / ".mcp.json", tools=("Edit",))
    denied = secret_denies(mode) + own_config + list(mode.write_tools) + list(CONTROL_TOOLS) \
        + ["Bash", "WebFetch", "WebSearch"]
    return allowed, list(dict.fromkeys(denied))


def unsafe_settings(vault: Path) -> str | None:
    """D15: a vault settings file that could run commands or widen permissions. Fails closed on bad JSON."""
    for name in SETTINGS_FILES:
        p = vault / ".claude" / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return f"{p.name} is unreadable"
        if not isinstance(data, dict):
            return f"{p.name} is not a JSON object"
        risky = sorted(RISKY_SETTINGS & set(data))
        if risky:
            return f"{p.name} sets {', '.join(risky)}"
    return None


def unsafe_git(vault: Path) -> str | None:
    """H3: a vault git config that makes git run programs (fsmonitor, hooksPath). Fails closed if unreadable."""
    cfg = vault / ".git" / "config"
    if not cfg.exists():
        return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ".git/config is unreadable"
    risky = [k for k in GIT_RISKY if k in text]
    return f".git/config sets {', '.join(risky)}" if risky else None


def refusal(vault: Path) -> str | None:
    bad = unsafe_settings(vault)
    return f".claude/{bad}" if bad else unsafe_git(vault)


def _run(cmd: list[str], cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess | str:
    try:
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "Claude Code timed out."
    except OSError as e:
        log_event(log, "claude_start_error", logging.ERROR, error_class=type(e).__name__)
        return "Claude Code could not start."


def _result(res: subprocess.CompletedProcess) -> str:
    try:
        return str(json.loads(res.stdout).get("result", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return res.stdout.strip()


def build_ask_cmd(mode: Mode, prompt: str) -> list[str]:
    allowed, denied = tool_policy(mode)
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--mcp-config", str(mode.mcp_config),
        "--strict-mcp-config",                      # ignore any user/global MCP servers
        "--settings", NO_HOOKS,                     # defense in depth for D15
        "--allowedTools", ",".join(allowed),
        "--disallowedTools", ",".join(denied),
    ]


@dataclass(frozen=True)
class Answer:
    text: str
    ok: bool


def ask_detailed(mode: Mode, prompt: str, agent: str | None = None, voice: bool = False,
                 timeout: int = 900) -> Answer:
    if agent:
        prompt = f"Use the {agent} subagent for this. {prompt}"
    if voice:
        prompt += "\n\nThis reply will be spoken. Under 60 words, no lists, no URLs."
    bad = refusal(mode.vault)
    if bad:
        log.error("refusing %s session: vault %s", mode.name, bad)
        return Answer(f"Refusing to run: the {mode.name} vault's {bad}. Remove it and try again.", False)
    res = _run(build_ask_cmd(mode, prompt), mode.vault, mode.subprocess_env(), timeout)
    if isinstance(res, str):
        return Answer(res, False)
    _log(mode, prompt, res.returncode)
    if res.returncode != 0:
        return Answer(f"Claude Code failed: {_redact(res.stderr).strip()[:300]}", False)
    return Answer(_result(res), True)


def ask(mode: Mode, prompt: str, agent: str | None = None, voice: bool = False, timeout: int = 900) -> str:
    return ask_detailed(mode, prompt, agent=agent, voice=voice, timeout=timeout).text


def plan_build(mode: Mode, repo_key: str, task_text: str) -> str:
    """Builder tasks run in the repo in plan mode only. Execution is a separate, human-triggered step."""
    repo = mode.repos[repo_key]
    cmd = ["claude", "-p", task_text, "--output-format", "json", "--permission-mode", "plan",
           "--mcp-config", str(mode.mcp_config), "--strict-mcp-config", "--settings", NO_HOOKS,
           "--disallowedTools", ",".join(secret_denies(mode) + list(mode.write_tools) + list(CONTROL_TOOLS)
                                         + ["WebFetch", "WebSearch"])]
    res = _run(cmd, repo, mode.subprocess_env(), 1800)
    if isinstance(res, str):
        return res
    return _result(res) if res.returncode == 0 else _redact(res.stderr)[:500]


def _log(mode: Mode, prompt: str, rc: int) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} rc={rc} {prompt[:120]!r}\n"
    try:
        logs = mode.vault / "agent-logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "runs.log", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        log_event(log, "runs_log_error", logging.WARNING, error_class=type(e).__name__)
