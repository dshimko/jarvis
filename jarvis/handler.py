"""Request handler shared by the API (voice) and the text channels. Control commands run in code, never the model."""
from __future__ import annotations
import logging, re
from dataclasses import dataclass
from typing import Callable
from . import brain, ofw_control, outbox, router
from .logsetup import log_event
from .modes import Mode, make_redactor

log = logging.getLogger(__name__)
APPROVE = re.compile(r"^(approve|send)\s+([\w-]+)(?:\s+([0-9a-fA-F]{8}))?(\s+reconfirm)?$", re.I)
PENDING = {"outbox", "pending", "what's pending", "whats pending", "what is pending", "anything pending"}
TRAILING = re.compile(r"[\s.?!,;:]+$")
VOICE_APPROVE_REFUSAL = "Voice approval uses the read-back flow in the tray app. Nothing was sent."
# AD38/AD39: OFW control commands, personal mode and text channels only, handled here and never by the model.
# PRIVILEGED_OK and OFW_RESET are used with fullmatch; [0-9] because \d matches any Unicode digit.
PRIVILEGED_OK = re.compile(r"privileged ok ([0-9]{1,20})", re.I)
PRIVILEGED_PREFIX = re.compile(r"privileged\s*ok", re.I)             # used with match: any such prefix
OFW_RESET = re.compile(r"ofw reset", re.I)
OFW_VOICE_REFUSAL = "OFW commands work only in Telegram, never by voice. Nothing was sent."
OFW_PERSONAL_ONLY = "OFW commands are personal-mode only."
OFW_NO_WRITE_TOKEN = "OFW write token not configured"
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


def _ofw_command(mode: Mode, norm: str, channel: str) -> str | None:
    """`privileged ok <id>` and `ofw reset`. Replies carry only the id and the server's status word."""
    is_privileged, is_reset = bool(PRIVILEGED_PREFIX.match(norm)), bool(OFW_RESET.fullmatch(norm))
    if not (is_privileged or is_reset):
        return None
    if channel == "voice":
        return OFW_VOICE_REFUSAL
    if mode.name != ofw_control.OFW_MODE:
        return OFW_PERSONAL_ONLY
    m = PRIVILEGED_OK.fullmatch(norm)
    if is_privileged and not m:
        return ofw_control.BAD_ID_REFUSAL              # the text never reaches the model
    if not ofw_control.has_write_token(mode):
        return OFW_NO_WRITE_TOKEN
    if m:
        return f"Privileged item {m.group(1)}: {ofw_control.confirm_privileged(mode, m.group(1))}."
    return f"OFW reset: {ofw_control.reset_breaker(mode)}."


def _control(mode: Mode, text: str, channel: str) -> str | None:
    """Returns a reply for a control command, or None when the text is not one."""
    norm = normalize(text)
    ofw = _ofw_command(mode, norm, channel)
    if ofw is not None:
        return ofw
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
