"""Outbox executor. The only code path that can write to Slack, email, or OFW.

Gates, all required, checked in order (check_gates):
  1. item.mode == executing mode, item.id == requested id, tool in that mode's write_tools
  2. status == approved, approved_at set, approved_sha256 == hash of the current item (D3/D13)
  3. created not in the future; age under max_age_hours, else marked expired
  4. daily cap for the mode, counted from the daemon-side send ledger (L3)
  5. Jev leak check (advisory, can only BLOCK): payload looks like other-mode content
  6. personal mode: Jev hostility over threshold requires a daemon-side reconfirm for this exact content (M2)
  7. server in the mode's repo MCP config (mcp/<mode>.mcp.json)
Then, under the per-mode lock, a compare-and-set approved -> sending, a direct MCP call (no LLM in the send
path) with the exact args from the file, then sent or failed (D16). Every approval binds the read-back:
readback_sha256 (API) or its 8-char code (text channels), so nothing executes unseen (D12/D17).
"""
from __future__ import annotations
import asyncio, hashlib, hmac, json, logging, re, threading, datetime as dt
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import yaml
from mcp import ClientSession
from . import jev, ledger
from .modes import CFG, Mode, make_redactor

log = logging.getLogger(__name__)
FM = re.compile(r"^---\n(.*?)\n---(?:\n(.*))?$", re.S)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PLAIN = re.compile(r"[\w.@/+-]{1,64}")
APPROVABLE = {"pending", "approved"}
FUTURE_SKEW = dt.timedelta(minutes=5)
TONE_FLAG = "tone flag"
CODE_LEN = 8
_LISTENERS: list[Callable[[str, str, str, str | None], None]] = []
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_REDACTOR: list[Callable[[str], str]] = []


class Blocked(Exception):
    pass


class NotFound(Blocked):
    pass


class Conflict(Blocked):
    pass


class BadReconfirm(Blocked):
    pass


@dataclass(frozen=True)
class Outcome:
    sent: bool
    message: str
    reason: str | None = None


# ---- wiring: listeners, redactor, per-mode lock ----

def add_listener(fn: Callable[[str, str, str, str | None], None]) -> None:
    _LISTENERS.append(fn)


def remove_listener(fn) -> None:
    if fn in _LISTENERS:
        _LISTENERS.remove(fn)


def _notify(kind: str, mode: str, item_id: str, reason: str | None = None) -> None:
    for fn in list(_LISTENERS):
        try:
            fn(kind, mode, item_id, reason)
        except Exception:
            log.exception("outbox listener failed")


def set_redactor(fn: Callable[[str], str] | None) -> None:
    """main wires the all-modes redactor; without it, each mode's own env values are redacted."""
    _REDACTOR.clear()
    if fn:
        _REDACTOR.append(fn)


def _redact(mode: Mode, text: str) -> str:
    return (_REDACTOR[0] if _REDACTOR else make_redactor({mode.name: mode}))(text)


def mode_lock(name: str) -> threading.RLock:
    """One lock per mode, shared by the API and the text channels (D9/M1)."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(name, threading.RLock())


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


# ---- approve ----

def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _load(mode: Mode, item_id: str) -> tuple[Path, dict, str]:
    path = item_path(mode, item_id)
    if not path.exists():
        raise NotFound("no such item")
    try:
        meta, body = read_item(path)
    except Blocked as e:
        raise Conflict(str(e)) from e
    return path, meta, body


def _check_approvable(mode: Mode, item_id: str, meta: dict) -> None:
    if meta.get("mode") != mode.name:
        raise Conflict("mode mismatch")
    if str(meta.get("id")) != item_id:
        raise Conflict("id mismatch")
    if meta.get("status") not in APPROVABLE:
        raise Conflict(f"status {meta.get('status')} is not approvable")


def _check_binding(meta: dict, body: str, body_hash: str | None, readback_hash: str | None,
                   code: str | None) -> None:
    """The approver must have seen the current read-back: its sha256 (API) or its 8-char code (text)."""
    if readback_hash is None and code is None:
        raise Conflict("no read-back code; read the item first")
    if body_hash is not None and body_hash != body_sha256(body):
        raise Conflict("body changed (hash mismatch)")
    if readback_hash is not None and readback_hash != readback_sha256(meta):
        raise Conflict("readback changed (hash mismatch)")
    if code is not None and (len(code) != CODE_LEN or not hmac.compare_digest(code.lower(), readback_code(meta))):
        raise Conflict("wrong code; read the item again to get the current code")


def _apply_reconfirm(mode: Mode, item_id: str, meta: dict, body: str, digest: str,
                     reconfirm: bool, strict: bool) -> None:
    if reconfirm:
        if strict and not tone_flagged(mode, meta, body):
            raise BadReconfirm("reconfirm is only valid after a tone flag")
        ledger.set_reconfirmed(mode.name, item_id, digest)
    elif ledger.is_reconfirmed(mode.name, item_id, digest):
        ledger.flag_tone(mode.name, item_id, digest)     # a plain re-approve does not carry a reconfirm


def approve(mode: Mode, item_id: str, reconfirm: bool = False, *, body_sha256: str | None = None,
            readback_sha256: str | None = None, code: str | None = None, strict_reconfirm: bool = False) -> Path:
    """Mark an item approved. Only pending/approved items (D1) of this mode and id (D2), bound to the
    read-back the approver saw. strict_reconfirm (API, D5): reconfirm only after a daemon-side tone flag."""
    with mode_lock(mode.name):
        path, meta, body = _load(mode, item_id)
        _check_approvable(mode, item_id, meta)
        _check_binding(meta, body, body_sha256, readback_sha256, code)
        try:
            digest = approval_hash(meta, body)
        except Blocked as e:
            raise Conflict(str(e)) from e
        _apply_reconfirm(mode, item_id, meta, body, digest, reconfirm, strict_reconfirm)
        new = {k: v for k, v in meta.items() if k not in ("reconfirmed", "approved_at", "approved_sha256")}
        write_item(path, {**new, "status": "approved", "approved_at": _now(), "approved_sha256": digest}, body)
        return path


# ---- gates ----

def _server_conf(mode: Mode, server: str) -> dict:
    try:
        servers = json.loads(mode.mcp_config.read_text(encoding="utf-8"))["mcpServers"]
    except (OSError, ValueError, KeyError) as e:
        raise Blocked(f"{mode.name} MCP config unreadable") from e
    conf = servers.get(server)
    if not conf or "url" not in conf:
        raise Blocked(f"server {server} not in {mode.name} config")
    sub = lambda s: re.sub(r"\$\{(\w+)\}", lambda m: mode.env.get(m.group(1), ""), s)
    return {"url": sub(conf["url"]), "headers": {k: sub(v) for k, v in conf.get("headers", {}).items()}}


@asynccontextmanager
async def _transport(conf: dict):
    """(read, write) streams for mcp 2.x (streamable_http_client) or 1.x (streamablehttp_client)."""
    try:
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
    except ImportError:
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(conf["url"], headers=conf["headers"]) as (r, w, *_):
            yield r, w
        return
    async with create_mcp_http_client(headers=conf["headers"]) as http:
        async with streamable_http_client(conf["url"], http_client=http) as (r, w, *_):
            yield r, w


async def _call(conf: dict, tool: str, args: dict):
    async with _transport(conf) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await s.call_tool(tool, args)


def _created(meta: dict) -> dt.datetime:
    try:
        c = dt.datetime.fromisoformat(str(meta["created"]))
    except (KeyError, ValueError) as e:
        raise Blocked("missing or invalid created") from e
    return c.astimezone().replace(tzinfo=None) if c.tzinfo else c


def _check_identity(mode: Mode, item_id: str, meta: dict) -> None:
    if meta.get("mode") != mode.name:
        raise Blocked("mode mismatch")
    if str(meta.get("id")) != item_id:
        raise Blocked("id mismatch")
    full_tool = f"mcp__{meta.get('server')}__{meta.get('tool')}"
    if full_tool not in mode.write_tools:
        raise Blocked(f"{full_tool} not an allowed write tool in {mode.name}")


def _check_approval(meta: dict, digest: str) -> None:
    if meta.get("status") != "approved" or not meta.get("approved_at") or not meta.get("approved_sha256"):
        raise Blocked("not approved")
    if meta["approved_sha256"] != digest:
        raise Blocked("changed after approval")


def _check_age(meta: dict) -> None:
    age = dt.datetime.now() - _created(meta)
    if age < -FUTURE_SKEW:
        raise Blocked("created in the future")
    if age > dt.timedelta(hours=CFG["outbox"]["max_age_hours"]):
        raise Blocked("expired")


def _check_jev(mode: Mode, item_id: str, meta: dict, body: str, digest: str) -> None:
    jc = CFG["jev"]
    other = "personal life, family, co-parenting, or OurFamilyWizard" if mode.name == "work" \
        else "Sparko business, clients, or work projects"
    qs = {"leak": jev.probability(f"Does this outgoing message contain or reference {other}?")}
    if mode.name == "personal":
        qs["hostile"] = jev.probability("Could a neutral reader or a family court judge read this message as hostile, sarcastic, or inflammatory?")
    d = jev.decide(mode.subprocess_env(), f"{body}\n\nARGS: {meta.get('args')}", qs)
    if float(d["leak"].value or 0) > jc["leak_block_threshold"]:
        raise Blocked("possible cross-mode content")
    if "hostile" in d and float(d["hostile"].value or 0) > jc["hostility_confirm_threshold"] \
            and not ledger.is_reconfirmed(mode.name, item_id, digest):
        ledger.flag_tone(mode.name, item_id, digest)
        raise Blocked(f"{TONE_FLAG}: reread and approve again with reconfirm")


def check_gates(mode: Mode, meta: dict, body: str, item_id: str | None = None) -> None:
    item_id = item_id if item_id is not None else str(meta.get("id"))
    _check_identity(mode, item_id, meta)
    digest = approval_hash(meta, body)
    _check_approval(meta, digest)
    _check_age(meta)
    if ledger.attempts_today(mode.name) >= mode.daily_write_cap:
        raise Blocked("daily cap reached")
    _check_jev(mode, item_id, meta, body, digest)


# ---- execute ----

def _block(path: Path, meta: dict, body: str, item_id: str, mode: Mode, reason: str) -> Outcome:
    reason = _redact(mode, reason)
    new = {**meta, "last_block": reason}
    if reason == "expired":
        new["status"] = "expired"
    if _unchanged(path, meta, body):                     # never clobber an edit made during the checks
        write_item(path, new, body)
    _notify("item_blocked", mode.name, item_id, reason)
    return Outcome(False, f"Blocked {item_id}: {reason}", reason)


def _send(path: Path, meta: dict, body: str, item_id: str, mode: Mode, conf: dict, args: dict) -> Outcome:
    sending = {**meta, "status": "sending", "attempted_at": _now()}
    write_item(path, sending, body)
    ledger.record_attempt(mode.name, item_id)
    try:
        result = asyncio.run(_call(conf, meta["tool"], args))
        err = f"server error: {result.content}" if getattr(result, "isError", False) else None
    except Exception as e:
        log.exception("MCP call failed for %s/%s", mode.name, item_id)
        err = f"send error: {type(e).__name__}"
    if err:
        err = _redact(mode, err)
        write_item(path, {**sending, "status": "failed", "last_block": err}, body)
        _notify("item_blocked", mode.name, item_id, err)
        return Outcome(False, f"Failed {item_id}: {err}. Resolve it in Obsidian.", err)
    write_item(path, {**sending, "status": "sent", "sent_at": _now()}, body)
    ledger.clear_tone(mode.name, item_id)
    _notify("item_sent", mode.name, item_id)
    return Outcome(True, f"Sent {item_id}.")


def _unchanged(path: Path, meta: dict, body: str) -> bool:
    try:
        return read_item(path) == (meta, body)
    except (Blocked, OSError, UnicodeDecodeError):
        return False


def execute_detailed(mode: Mode, item_id: str) -> Outcome:
    with mode_lock(mode.name):
        path = item_path(mode, item_id)
        try:
            meta, body = read_item(path)
        except OSError:
            return Outcome(False, f"No outbox item {item_id}.", "no such item")
        except Blocked as e:
            return Outcome(False, f"Blocked {item_id}: {e}", str(e))
        try:
            check_gates(mode, meta, body, item_id)
            conf, args = _server_conf(mode, meta["server"]), parse_args(meta)
        except Blocked as e:
            return _block(path, meta, body, item_id, mode, str(e))
        if not _unchanged(path, meta, body):             # compare-and-set approved -> sending (M1)
            _notify("item_blocked", mode.name, item_id, "changed during checks")
            return Outcome(False, f"Blocked {item_id}: changed during checks", "changed during checks")
        return _send(path, meta, body, item_id, mode, conf, args)


def execute(mode: Mode, item_id: str) -> str:
    return execute_detailed(mode, item_id).message


def approve_and_execute(mode: Mode, item_id: str, reconfirm: bool = False, **binding) -> Outcome:
    """approve() then execute under one per-mode lock. approve() errors propagate (NotFound/Conflict/...)."""
    with mode_lock(mode.name):
        approve(mode, item_id, reconfirm, **binding)
        return execute_detailed(mode, item_id)


# ---- listing ----

def pending_items(mode: Mode) -> list[tuple[dict, str, Path]]:
    return [(meta, body, p) for meta, body, p in _items(mode) if meta.get("status") == "pending"]


def pending(mode: Mode) -> list[tuple[str, str]]:
    return [(str(meta.get("id")), body) for meta, body, _ in pending_items(mode)]


def reviewable(mode: Mode) -> list[tuple[dict, str, Path]]:
    """Items the client can act on: pending, or approved and tone-flagged by the daemon (needs reconfirm)."""
    return [(meta, body, p) for meta, body, p in _items(mode)
            if meta.get("status") == "pending" or tone_flagged(mode, meta, body)]


def read_card(mode: Mode, item_id: str) -> str:
    """Text-channel `read <id>`: the exact read-back plus the code that `approve <id> <code>` needs (H1)."""
    try:
        _, meta, _ = _load(mode, item_id)
        _check_approvable(mode, item_id, meta)
    except Blocked:
        return "No approvable item with that id."
    return f"{render_readback(meta)}\ncode {readback_code(meta)}"
