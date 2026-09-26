"""Personal daemon only (AD10): a one-line Telegram notice for each new outbox draft.

The notice carries the id and the draft's first line; the full body and read-back travel only through the
existing `read <id>` command. send_text (AD39) carries fixed strings only, such as the OFW breaker notice,
which holds at most an error class. Sends go straight to the Bot API over httpx (whose INFO logs, which include
the bot token in the URL, are pinned to WARNING by logsetup). Errors log error_class only.
"""
from __future__ import annotations
import logging, threading
from typing import Callable
import httpx
from .logsetup import log_event
from .modes import Mode

log = logging.getLogger(__name__)
API_URL = "https://api.telegram.org/bot{token}/sendMessage"
NOTICE = "New draft `{id}`: `{first_line}`. Send `read {id}` to review."
SEND_TIMEOUT_SECONDS = 10.0
MAX_FIRST_LINE = 200
HTTP_OK_MIN, HTTP_OK_MAX = 200, 299
KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID")


class TelegramPush:
    def __init__(self, mode: Mode, redact: Callable[[str], str], post: Callable = httpx.post,
                 background: bool = True):
        self.mode, self.redact, self._post, self._background = mode, redact, post, background
        self._url = API_URL.format(token=mode.env["TELEGRAM_BOT_TOKEN"])
        self._chat = int(mode.env["TELEGRAM_OWNER_CHAT_ID"])

    def notice(self, data: dict) -> str:
        first = self.redact(str(data.get("first_line") or ""))[:MAX_FIRST_LINE]
        return NOTICE.format(id=data.get("id"), first_line=first)

    def on_event(self, kind: str, data: dict) -> None:
        if kind != "pending_new" or data.get("mode") != self.mode.name:
            return
        if self._background:
            threading.Thread(target=self._send, args=(data,), daemon=True, name="telegram-push").start()
        else:
            self._send(data)

    def send_text(self, text: str) -> bool:
        """Content-free by contract: callers pass fixed strings carrying at most an error class (AD39).
        Synchronous; True when Telegram accepted it. Redacted anyway."""
        return self._post_text(self.redact(text))

    def _send(self, data: dict) -> None:
        self._post_text(self.notice(data), id=data.get("id"))

    def _post_text(self, text: str, **meta) -> bool:
        try:
            resp = self._post(self._url, json={"chat_id": self._chat, "text": text}, timeout=SEND_TIMEOUT_SECONDS)
        except Exception as e:                            # never the exception text: it can carry the URL
            log_event(log, "telegram_push_error", logging.ERROR, error_class=type(e).__name__, **meta)
            return False
        status = getattr(resp, "status_code", None)
        if status is not None and not HTTP_OK_MIN <= status <= HTTP_OK_MAX:
            log_event(log, "telegram_push_error", logging.ERROR, error_class="HTTPStatus", status=status, **meta)
            return False
        return True


def start(mode: Mode, bus, redact: Callable[[str], str], **kw) -> TelegramPush | None:
    """Subscribe to the bus, or return None (logged) when the Telegram keys are empty or invalid."""
    if not all(mode.env.get(k) for k in KEYS):
        log_event(log, "telegram_push_disabled", logging.WARNING, error_class="MissingKeys")
        return None
    try:
        push = TelegramPush(mode, redact, **kw)
    except ValueError:
        log_event(log, "telegram_push_disabled", logging.WARNING, error_class="BadOwnerChatId")
        return None
    bus.add_listener(push.on_event)
    return push
