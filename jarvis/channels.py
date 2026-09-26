"""Text channels. Work: Slack DM to the Jarvis bot in the Sparko workspace. Personal: Telegram bound to one chat.
Each channel is hard-bound to its mode; router never overrides that. A channel with empty tokens is skipped (D10)."""
from __future__ import annotations
import asyncio, logging, threading
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from .logsetup import log_event

log = logging.getLogger(__name__)
SLACK_KEYS = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_OWNER_USER_ID")
TELEGRAM_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID")


def _missing(env: dict, keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if not env.get(k)]


def start_slack(mode, handle) -> bool:
    env = mode.env
    if missing := _missing(env, SLACK_KEYS):
        log.warning("Slack channel skipped: empty %s", ", ".join(missing))
        return False
    try:
        app = App(token=env["SLACK_BOT_TOKEN"])
    except Exception as e:
        log.error("Slack channel skipped: %s", type(e).__name__)
        return False

    @app.event("message")
    def on_msg(event, say):
        if event.get("user") != env["SLACK_OWNER_USER_ID"] or event.get("channel_type") != "im":
            return  # only you, only DMs
        say(handle(event.get("text", ""), "slack_work"))

    threading.Thread(target=SocketModeHandler(app, env["SLACK_APP_TOKEN"]).start, daemon=True).start()
    return True


def start_telegram(mode, handle) -> bool:
    env = mode.env
    if missing := _missing(env, TELEGRAM_KEYS):
        log.warning("Telegram channel skipped: empty %s", ", ".join(missing))
        return False
    try:
        owner = int(env["TELEGRAM_OWNER_CHAT_ID"])
    except ValueError:
        log.warning("Telegram channel skipped: TELEGRAM_OWNER_CHAT_ID is not an integer")
        return False

    async def on_msg(update: Update, _: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != owner:
            return
        reply = await asyncio.to_thread(handle, update.message.text or "", "telegram")
        await update.message.reply_text(reply[:4000])

    def run():
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            app = Application.builder().token(env["TELEGRAM_BOT_TOKEN"]).build()
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_msg))
            app.run_polling(stop_signals=None)
        except Exception as e:                       # no traceback: it can carry the bot token or a message
            log_event(log, "telegram_stopped", logging.ERROR, channel="telegram", error_class=type(e).__name__)

    threading.Thread(target=run, daemon=True).start()
    return True
