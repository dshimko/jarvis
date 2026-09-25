"""WSL <-> Windows path helpers: WIN_HOME / LOCALAPPDATA resolution, wslpath, Obsidian URIs."""
from __future__ import annotations
import functools, logging, os, re, subprocess
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)
INTEROP_TIMEOUT = 10
CMD_EXE = "/mnt/c/Windows/System32/cmd.exe"   # absolute: the daemon PATH has no Windows dirs (B8)
MNT = re.compile(r"^/mnt/([a-zA-Z])(?:/(.*))?$")


def jarvis_dir() -> Path:
    return Path.home() / ".jarvis"


def _hint(name: str) -> str | None:
    """One-line hint file written by install_wsl.sh, e.g. ~/.jarvis/win_home."""
    try:
        line = (jarvis_dir() / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return line or None


def _run(cmd: list[str]) -> str | None:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=INTEROP_TIMEOUT, cwd="/")
    except (OSError, subprocess.SubprocessError):
        return None
    out = res.stdout.strip()
    return out if res.returncode == 0 and out else None


@functools.lru_cache(maxsize=None)
def _interop(var: str) -> str | None:
    """Ask Windows for %VAR% via interop and convert it to a WSL path."""
    win = _run([CMD_EXE, "/c", f"echo %{var}%"])
    if not win or win == f"%{var}%":
        return None
    return _run(["wslpath", "-u", win])


def _resolve(env_key: str, hint: str, var: str) -> Path | None:
    value = os.environ.get(env_key) or _hint(hint) or _interop(var)
    return Path(value) if value else None


def windows_home() -> Path | None:
    return _resolve("JARVIS_WIN_HOME", "win_home", "USERPROFILE")


def windows_localappdata() -> Path | None:
    return _resolve("JARVIS_WIN_LOCALAPPDATA", "win_localappdata", "LOCALAPPDATA")


def jarvis_localappdata() -> Path | None:
    """%LOCALAPPDATA%\\Jarvis as a WSL path, or None when Windows is unresolvable."""
    base = windows_localappdata()
    return base / "Jarvis" if base else None


def expand(value: str) -> str:
    """Expand ${WIN_HOME} and ~. Raises ValueError when WIN_HOME is needed but unresolvable."""
    if "${WIN_HOME}" in value:
        home = windows_home()
        if home is None:
            raise ValueError("cannot resolve ${WIN_HOME} (the Windows user folder). Set JARVIS_WIN_HOME, or write "
                             "e.g. /mnt/c/Users/<you> to ~/.jarvis/win_home, or check that WSL interop "
                             f"({CMD_EXE}) works")
        value = value.replace("${WIN_HOME}", str(home))
    return os.path.expanduser(value)


def _mnt_to_windows(p: str) -> str | None:
    m = MNT.match(p)
    if not m:
        return None
    rest = (m.group(2) or "").replace("/", "\\")
    return f"{m.group(1).upper()}:\\{rest}"


def to_windows(p: Path | str) -> str | None:
    """Windows form of a WSL path. /mnt/<d>/ is converted in pure Python; anything else asks wslpath."""
    s = str(p)
    return _mnt_to_windows(s) or _run(["wslpath", "-w", s])


def obsidian_uri(vault: Path, file: str) -> str:
    """obsidian://open?vault=<vault dir name>&file=<vault-relative path>."""
    return f"obsidian://open?vault={quote(Path(vault).name, safe='')}&file={quote(file, safe='')}"
