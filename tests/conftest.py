"""Shared fixtures: tmp repo + tmp vaults, fake secrets, mocked Jev and MCP. No network, no real env files."""
from __future__ import annotations
import datetime as dt, json, shutil
from dataclasses import replace
from pathlib import Path
import pytest
import yaml
from jarvis import jev, outbox
from jarvis.modes import CFG, Mode

REPO = Path(__file__).resolve().parent.parent
TOKEN = "ab" * 32
WORK_ENV = {"SLACK_MCP_URL": "https://slack.example.test/mcp", "SLACK_WORK_TOKEN": "xoxp-work-secret-1111",
            "GMAIL_MCP_URL": "https://gmail-work.example.test/mcp", "GMAIL_WORK_TOKEN": "gmail-work-secret-2222",
            "SLACK_BOT_TOKEN": "", "SLACK_APP_TOKEN": "", "SLACK_OWNER_USER_ID": ""}
PERSONAL_ENV = {"GMAIL_MCP_URL": "https://gmail-personal.example.test/mcp",
                "GMAIL_PERSONAL_TOKEN": "gmail-personal-secret-3333",
                "OFW_MCP_URL": "https://ofw.example.test/mcp", "OFW_MCP_TOKEN": "ofw-personal-secret-4444",
                "OFW_MCP_WRITE_TOKEN": "ofw-write-secret-5555",
                "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_CHAT_ID": ""}
PERSONAL_SECRETS = [v for v in PERSONAL_ENV.values() if len(v) >= 8]
WORK_SECRETS = [v for v in WORK_ENV.values() if len(v) >= 8]


def _mode(name: str, vault: Path, env: dict, root: Path) -> Mode:
    m = CFG["modes"][name]
    return Mode(name=name, vault=vault, env=dict(env), channels=m["channels"], daily_write_cap=m["daily_write_cap"],
                agents=m["agents"], read_tools=m["read_tools"], write_tools=m["write_tools"], root=root)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("JARVIS_WIN_HOME", raising=False)
    monkeypatch.delenv("JARVIS_WIN_LOCALAPPDATA", raising=False)
    monkeypatch.setattr("jarvis.paths._interop", lambda var: None)
    return h


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "repo"
    (r / "mcp").mkdir(parents=True)
    (r / "env").mkdir()
    for name in ("work", "personal"):
        shutil.copy(REPO / "mcp" / f"{name}.mcp.json", r / "mcp" / f"{name}.mcp.json")
    return r


@pytest.fixture
def modes(tmp_path, root, home):
    vaults = tmp_path / "win" / "Vaults"
    work_v, pers_v = vaults / "Jarvis-Work", vaults / "Jarvis-Personal"
    for v in (work_v, pers_v):
        (v / "outbox").mkdir(parents=True)
        (v / "agent-logs").mkdir()
    work = _mode("work", work_v, WORK_ENV, root)
    personal = _mode("personal", pers_v, PERSONAL_ENV, root)
    return {"work": replace(work, peers=(pers_v,)), "personal": replace(personal, peers=(work_v,))}


def now_iso(delta: dt.timedelta = dt.timedelta()) -> str:
    return (dt.datetime.now() + delta).isoformat(timespec="seconds")


DEFAULTS = {
    "work": {"server": "slack", "tool": "post_message", "args": {"channel": "C123", "text": "See you at 5"}},
    "personal": {"server": "ofw", "tool": "send_message", "args": {"to": "coparent", "body": "Pickup at 5 works."}},
}


def make_item(mode: Mode, item_id: str = "20260924-100000-hello", body: str = "See you at 5", *,
              newline: str = "\n", bom: bool = False, **meta) -> Path:
    base = {"id": item_id, "mode": mode.name, **DEFAULTS[mode.name], "status": "pending", "created": now_iso()}
    full = {**base, **meta}
    if isinstance(full.get("args"), dict):
        full["args"] = json.dumps(full["args"])
    text = f"---\n{yaml.safe_dump(full, sort_keys=False).strip()}\n---\n{body}\n".replace("\n", newline)
    path = mode.vault / "outbox" / f"{item_id}.md"
    path.write_bytes((("﻿" if bom else "") + text).encode("utf-8"))
    return path


def meta_of(path: Path) -> dict:
    return outbox.read_item(path)[0]


class FakeResult:
    """An MCP CallToolResult. structuredContent carries the ofw status (AD35); other servers ignore it."""
    def __init__(self, is_error: bool = False, content="ok", structured: dict | None = None):
        self.isError, self.content = is_error, content
        self.structuredContent = {"status": "sent"} if structured is None else structured


@pytest.fixture
def jev_scores(monkeypatch):
    """Controls every Jev answer. Tests set scores['hostile'] etc.; mode/agent default to unclear/none."""
    scores = {"leak": 0.0, "hostile": 0.0, "mode": ("unclear", 0.0), "agent": ("none", 0.0)}

    def decide(env, state, questions):
        out = {}
        for k in questions:
            v = scores.get(k, 0.0)
            value, conf = v if isinstance(v, tuple) else (v, 1.0)
            out[k] = jev.Decision({"value": value, "confidence": conf})
        return out
    monkeypatch.setattr(jev, "decide", decide)
    return scores


@pytest.fixture
def mcp_calls(monkeypatch):
    """Replaces the MCP _call. Set calls.result / calls.error to shape the response."""
    class Calls(list):
        result = FakeResult()
        error: Exception | None = None
        on_call = None

    calls = Calls()

    async def fake_call(conf, tool, args):
        calls.append({"conf": conf, "tool": tool, "args": args})
        if calls.on_call:
            calls.on_call()
        if calls.error:
            raise calls.error
        return calls.result
    monkeypatch.setattr(outbox, "_call", fake_call)
    return calls


def approve_ok(mode: Mode, item_id: str, reconfirm: bool = False, **kw):
    """approve() bound to the item's current read-back (what a real approver would have seen)."""
    try:
        meta = outbox.read_item(outbox.item_path(mode, item_id))[0]
        kw.setdefault("readback_sha256", outbox.readback_sha256(meta))
    except Exception:
        kw.setdefault("code", "00000000")
    return outbox.approve(mode, item_id, reconfirm, **kw)
