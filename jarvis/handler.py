"""Request handler shared by the API (voice) and the text channels. Control commands run in code, never the model."""
from __future__ import annotations
import logging, re
from dataclasses import dataclass
from typing import Callable
from . import brain, outbox, router
from .logsetup import log_event
from .modes import Mode, make_redactor

log = logging.getLogger(__name__)
APPROVE = re.compile(r"^(approve|send)\s+([\w-]+)(?:\s+([0-9a-fA-F]{8}))?(\s+reconfirm)?$", re.I)
PENDING = {"outbox", "pending", "what's pending", "whats pending", "what is pending", "anything pending"}
TRAILING = re.compile(r"[\s.?!,;:]+$")
VOICE_APPROVE_REFUSAL = "Voice approval uses the read-back flow in the tray app. Nothing was sent."
NOT_SERVED = "That is a {mode} request; this Jarvis serves {served}. Confirm to send it to {mode}."


@dataclass(frozen=True)
class Reply:
    text: str
    mode: str | None
    needs_mode: bool = False
    suggested_mode: str | None = None       # AD15: the other mode, when routing disagreed with the hotkey


def normalize(text: str) -> str:
    """D7: curly apostrophes, whitespace, trailing punctuation. Case is kept (ids are case-sensitive)."""
    return TRAILING.sub("", " ".join(text.replace("’", "'").split()))


def _pending(mode: Mode) -> str:
    items = outbox.pending(mode)
    return "Nothing pending." if not items else "Pending: " + "; ".join(i for i, _ in items)


def _read(mode: Mode, item_id: str) -> str:
    return outbox.read_card(mode, item_id)


def _approve(mode: Mode, item_id: str, code: str | None, reconfirm: bool) -> str:
    """Text approve must quote the code from `read <id>` (H1), so the read-back is what gets approved."""
    if not code:
        return (f"Not approved. Send `read {item_id}` first, then `approve {item_id} <code>` "
                "with the code it shows.")
    try:
        return outbox.approve_and_execute(mode, item_id, reconfirm, code=code, strict_reconfirm=True).message
    except outbox.BadReconfirm:
        return ("reconfirm is only accepted after the tone check flags an item; "
                "approve without reconfirm first")
    except outbox.Blocked as e:
        return f"Not approved {item_id}: {e}"
    except Exception as e:
        log_event(log, "approve_error", logging.ERROR, id=item_id, error_class=type(e).__name__)
        return f"Blocked {item_id}: internal error"


def _control(mode: Mode, text: str, channel: str) -> str | None:
    """Returns a reply for a control command, or None when the text is not one."""
    norm = normalize(text)
    if norm.lower() in PENDING:
        return _pending(mode)
    if norm.lower().startswith("read "):
        return _read(mode, norm[5:].strip())
    if m := APPROVE.match(norm):
        if channel == "voice":
            return VOICE_APPROVE_REFUSAL      # D8: voice approval only via /approve + hashes
        return _approve(mode, m.group(2), m.group(3), reconfirm=bool(m.group(4)))
    return None


def make_handler(modes: dict[str, Mode], redact: Callable[[str], str] | None = None):
    """Returns (handle_detailed, handle). handle() is the str-only form the text channels use."""
    redact = redact or make_redactor(modes)

    def handle_detailed(text: str, channel: str, voice_mode: str | None = None,
                        mode_confirmed: bool = False) -> Reply:
        text = (text or "").strip()
        if not text:
            return Reply("I didn't catch that.", voice_mode)
        if channel == "voice" and voice_mode in modes:
            # D7: control commands use the hotkey mode directly, no Jev.
            reply = _control(modes[voice_mode], router.strip_prefix(text), channel) \
                if router.spoken_prefix(text) in (None, voice_mode) else None
            if reply is not None:
                return Reply(redact(reply), voice_mode)
        env = modes[voice_mode].subprocess_env() if voice_mode in modes else {}
        mode_name, why, suggested = router.pick_route(text, channel, voice_mode, env, mode_confirmed)
        if mode_name is None:
            return Reply(f"Which mode, work or personal? ({why})", None, needs_mode=True, suggested_mode=suggested)
        if mode_name not in modes:                           # AD4: the other mode is the other daemon
            served = ", ".join(modes)
            return Reply(NOT_SERVED.format(mode=mode_name, served=served), None, needs_mode=True,
                         suggested_mode=mode_name)
        mode = modes[mode_name]
        reply = _control(mode, text, channel)
        if reply is None:
            reply = brain.ask(mode, text, agent=router.pick_agent(text, mode), voice=channel == "voice")
        return Reply(redact(reply), mode_name)

    def handle(text: str, channel: str) -> str:
        return handle_detailed(text, channel).text

    return handle_detailed, handle
