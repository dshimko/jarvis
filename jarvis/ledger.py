"""Daemon-side state under ~/.jarvis (WSL ext4, 0600). Nothing here is read from the vault, which the agent
and Windows can write. Tone flags / reconfirms (M2) and the send log that drives the daily cap (L3)."""
from __future__ import annotations
import datetime as dt, fcntl, json, logging, os, tempfile, threading
from contextlib import contextmanager
from pathlib import Path
from . import paths

log = logging.getLogger(__name__)
_LOCK = threading.RLock()
TONE_FILE = "tone_flags.json"
SENDS_FILE = "sends.log"
APPROVE_KEY_FILE = "approve_code.key"
APPROVE_KEY_LEN = 32
LOCK_FILE = ".ledger.lock"


def atomic_write_text(path: Path, text: str) -> None:
    """mkstemp in the same dir (0600) + os.replace, so readers never see a partial file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _dir() -> Path:
    d = paths.jarvis_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def approve_code_key() -> bytes:
    """32 random bytes for the text-channel approve-code HMAC, at ~/.jarvis/approve_code.key (0600).
    Created once via O_EXCL on first use; every later call reuses the same key."""
    path = _dir() / APPROVE_KEY_FILE
    with _LOCK:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(os.urandom(APPROVE_KEY_LEN))
            except BaseException:
                path.unlink(missing_ok=True)
                raise
    return path.read_bytes()


# ---- tone flags: {mode: {id: {"hash": approval_hash, "reconfirmed": bool}}} ----

def _load_tone() -> dict:
    try:
        data = json.loads((_dir() / TONE_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.error("tone ledger unreadable; treating every item as unflagged")
        return {}
    return data if isinstance(data, dict) else {}


def _save_tone(data: dict) -> None:
    atomic_write_text(_dir() / TONE_FILE, json.dumps(data, sort_keys=True))


@contextmanager
def _file_lock():
    """Cross-process lock: on WSL both per-mode daemons share ~/.jarvis (AD4), so a thread lock is not enough."""
    fd = os.open(_dir() / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                                      # closing releases the flock


def _update(mode: str, item_id: str, entry: dict | None) -> None:
    with _LOCK, _file_lock():
        data = _load_tone()
        per_mode = {k: v for k, v in data.get(mode, {}).items() if k != item_id}
        if entry is not None:
            per_mode[item_id] = entry
        _save_tone({**data, mode: per_mode})


def _entry(mode: str, item_id: str, digest: str) -> dict | None:
    with _LOCK:
        e = _load_tone().get(mode, {}).get(item_id)
    return e if isinstance(e, dict) and e.get("hash") == digest else None


def flag_tone(mode: str, item_id: str, digest: str) -> None:
    _update(mode, item_id, {"hash": digest, "reconfirmed": False})


def is_tone_flagged(mode: str, item_id: str, digest: str) -> bool:
    """True only if the daemon flagged this exact item content (hash) and it is not yet reconfirmed."""
    e = _entry(mode, item_id, digest)
    return bool(e) and not e.get("reconfirmed")


def set_reconfirmed(mode: str, item_id: str, digest: str) -> None:
    _update(mode, item_id, {"hash": digest, "reconfirmed": True})


def is_reconfirmed(mode: str, item_id: str, digest: str) -> bool:
    e = _entry(mode, item_id, digest)
    return bool(e) and bool(e.get("reconfirmed"))


def clear_tone(mode: str, item_id: str) -> None:
    _update(mode, item_id, None)


# ---- send log (append-only): "<date>\t<mode>\t<id>" per attempt ----

def record_attempt(mode: str, item_id: str) -> None:
    line = f"{dt.date.today().isoformat()}\t{mode}\t{item_id}\n"
    with _LOCK:
        fd = os.open(_dir() / SENDS_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line)


def attempts_today(mode: str) -> int:
    today = dt.date.today().isoformat()
    try:
        lines = (_dir() / SENDS_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return 0
    return sum(1 for line in lines if line.split("\t")[:2] == [today, mode])
