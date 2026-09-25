"""Routes an utterance to a mode and a subagent. Channel or hotkey binds mode; Jev can only ask, never switch."""
from __future__ import annotations
import logging, re
from . import jev
from .modes import CFG, Mode

log = logging.getLogger(__name__)
CHANNEL_MODE = {"slack_work": "work", "telegram": "personal"}
MODE_NAMES = ("work", "personal")
PREFIX = re.compile(r"^\s*(?:jarvis[\s,]+)?(work|personal)\b[\s,.:;!-]*", re.I)


def spoken_prefix(text: str) -> str | None:
    m = PREFIX.match(text)
    return m.group(1).lower() if m else None


def strip_prefix(text: str) -> str:
    return PREFIX.sub("", text, count=1) if spoken_prefix(text) else text


def _jev_mode(text: str, env: dict):
    try:
        return jev.decide(env, text, {"mode": jev.choice(
            "Is this request about Sparko work (clients, Slack, work email, code, Jira) or personal life "
            "(personal email, co-parenting, OFW, family)?", ["work", "personal", "unclear"])})["mode"]
    except Exception:
        log.warning("jev mode check unavailable; using the bound mode")
        return None


def pick_mode(text: str, channel: str, bound_mode: str | None, env: dict,
              mode_confirmed: bool = False) -> tuple[str | None, str]:
    """(mode, reason). None means ask the human. D6: voice is hotkey-bound; a disagreeing prefix or a
    confident Jev disagreement returns None, and only the matching hotkey (mode_confirmed) settles it."""
    if channel in CHANNEL_MODE:
        return CHANNEL_MODE[channel], "bound by channel"
    if channel != "voice" or bound_mode not in MODE_NAMES:
        return None, "unknown channel"
    prefix = spoken_prefix(text)
    if prefix and prefix != bound_mode:
        return None, f"you said {prefix} but the hotkey was {bound_mode}"
    if prefix or mode_confirmed:
        return bound_mode, "hotkey"
    d = _jev_mode(text, env)
    if d is not None and d.value in MODE_NAMES and d.value != bound_mode \
            and d.confidence >= CFG["jev"]["mode_confidence_min"]:
        return None, f"sounds like {d.value} but hotkey was {bound_mode}"
    return bound_mode, "hotkey"


def pick_agent(text: str, mode: Mode) -> str | None:
    try:
        d = jev.decide(mode.subprocess_env(), text, {"agent": jev.choice(
            "Which specialist should handle this request?", mode.agents + ["none"])})["agent"]
    except Exception:
        log.warning("jev agent pick unavailable")
        return None
    if d.value in mode.agents and d.confidence >= CFG["jev"]["agent_confidence_min"]:
        return d.value
    return None  # let Claude Code's main session decide delegation itself
