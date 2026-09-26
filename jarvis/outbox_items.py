"""Outbox item files: errors, parsing, and the hashes and read-back that approval binds (D3, D12, D13).
Split out of outbox.py; outbox re-exports every public name here."""
from __future__ import annotations
import hashlib, hmac, json, logging, re
from pathlib import Path
import yaml
from . import ledger
from .modes import Mode

log = logging.getLogger("jarvis.outbox")
FM = re.compile(r"^---\n(.*?)\n---(?:\n(.*))?$", re.S)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PLAIN = re.compile(r"[\w.@/+-]{1,64}")
CODE_LEN = 8
SYNC_CONFLICT_MARK = ".sync-conflict-"


class Blocked(Exception):
    pass


class NotFound(Blocked):
    pass


class Conflict(Blocked):
    pass


class BadReconfirm(Blocked):
    pass


# ---- file io ----

def normalize(text: str) -> str:
    """B2: strip a BOM and turn CRLF / lone CR into LF."""
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def item_path(mode: Mode, item_id: str) -> Path:
    """D4: strict id pattern and containment inside <vault>/outbox."""
    if not isinstance(item_id, str) or not ID_RE.match(item_id):
        raise NotFound("invalid id")
    base = (mode.vault / "outbox").resolve()
    path = (base / f"{item_id}.md").resolve()
    if path.parent != base:
        raise NotFound("invalid id")
    return path


def read_item(path: Path) -> tuple[dict, str]:
    m = FM.match(normalize(path.read_text(encoding="utf-8")))
    if not m:
        raise Blocked("malformed outbox item")
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise Blocked("malformed outbox item") from e
    if not isinstance(meta, dict):
        raise Blocked("malformed outbox item")
    return meta, (m.group(2) or "").strip()


def write_item(path: Path, meta: dict, body: str) -> None:
    text = f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()}\n---\n{body}\n"
    ledger.atomic_write_text(path, text)


def _items(mode: Mode):
    for p in sorted((mode.vault / "outbox").glob("*.md")):
        if SYNC_CONFLICT_MARK in p.name:                  # Syncthing copies are not items (AD9 blocks the original)
            continue
        try:
            meta, body = read_item(p)
        except (Blocked, OSError, UnicodeDecodeError):
            continue
        yield meta, body, p


# ---- hashes and readback ----

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compact(v) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def parse_args(meta: dict) -> dict:
    raw = meta.get("args")
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as e:
        raise Blocked("args are not valid JSON") from e
    if not isinstance(args, dict):
        raise Blocked("args must be a JSON object")
    if not all(isinstance(k, str) for k in args):
        raise Blocked("args keys must be strings")
    return args


def _label(v) -> str:
    s = v if isinstance(v, str) else _compact(v)
    return s if PLAIN.fullmatch(s) else json.dumps(s, ensure_ascii=False)


def render_readback(meta: dict) -> str:
    """D12: deterministic text of what will actually be sent. Values are JSON-encoded (L1) so an embedded
    newline cannot fake a line. Never raises on agent-written data (M4)."""
    try:
        head = [f"{h}: {_label(meta.get(h))}" for h in ("mode", "server", "tool")]
        try:
            args = parse_args(meta)
        except Blocked:
            return "\n".join(head + [f"args (unparseable): {json.dumps(str(meta.get('args')), ensure_ascii=False)}"])
        return "\n".join(head + [f"{_label(k)}: {_compact(v)}" for k, v in sorted(args.items())])
    except Exception:
        log.warning("readback render failed")
        return "unreadable item"


def body_sha256(body: str) -> str:
    return sha256(body)


def readback_sha256(meta: dict) -> str:
    return sha256(render_readback(meta))


def readback_code(meta: dict) -> str:
    """8-char HMAC-SHA256(key, readback_sha256) so an attacker cannot precompute a colliding code (M5)."""
    key = ledger.approve_code_key()
    return hmac.new(key, readback_sha256(meta).encode("ascii"), hashlib.sha256).hexdigest()[:CODE_LEN]


def approval_hash(meta: dict, body: str) -> str:
    """D13: binds everything the executor uses, plus created (so expiry can't be reset)."""
    return sha256(_compact({"mode": meta.get("mode"), "server": meta.get("server"), "tool": meta.get("tool"),
                            "args": parse_args(meta), "body": body, "created": str(meta.get("created"))}))


def tone_flagged(mode: Mode, meta: dict, body: str) -> bool:
    """M2: from the daemon ledger only; last_block in the file is informational."""
    try:
        digest = approval_hash(meta, body)
    except Blocked:
        return False
    return meta.get("status") == "approved" and ledger.is_tone_flagged(mode.name, str(meta.get("id")), digest)
