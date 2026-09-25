"""Jarvis daemon (headless, WSL): local API for the tray client + Slack (work) + Telegram (personal) + schedules."""
from __future__ import annotations
import argparse, logging
from apscheduler.schedulers.background import BackgroundScheduler
from . import api, brain, channels, events, outbox
from .handler import make_handler
from .modes import CFG, Mode, load_modes, make_redactor

log = logging.getLogger("jarvis")
MISFIRE_GRACE_SECONDS = 3600      # WSL sleeps with Windows; run a missed job late (once) rather than skip it


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jarvis", description="Jarvis daemon (always headless)")
    p.add_argument("--headless", action="store_true", help="accepted for explicitness; the daemon is always headless")
    p.add_argument("--no-channels", action="store_true", help="do not start Slack or Telegram")
    return p.parse_args(argv)


def run_job(mode: Mode, job: str, bus: events.EventBus) -> None:
    """One scheduled command; always publishes schedule_done (ok false on failure)."""
    try:
        ok = brain.ask_detailed(mode, f"/{job}").ok
    except Exception:
        ok = False
        log.exception("scheduled job %s/%s failed", mode.name, job)
    if not ok:
        log.warning("scheduled job %s/%s did not complete", mode.name, job)
    bus.publish("schedule_done", {"mode": mode.name, "job": job, "ok": ok})


def schedules(modes: dict[str, Mode], bus: events.EventBus, cfg: dict = CFG) -> BackgroundScheduler:
    s = BackgroundScheduler(job_defaults={"misfire_grace_time": MISFIRE_GRACE_SECONDS, "coalesce": True})
    for mode_name, jobs in (cfg.get("schedule") or {}).items():
        for job, at in jobs.items():
            h, m = at.split(":")
            s.add_job(run_job, "cron", args=(modes[mode_name], job, bus), hour=int(h), minute=int(m),
                      day_of_week="mon-fri" if mode_name == "work" else "*")
    return s


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = api.api_settings(CFG)                       # C7: fail before anything starts
    modes = load_modes()
    redact = make_redactor(modes)
    outbox.set_redactor(redact)                            # M3: nothing the executor writes carries a secret
    brain.set_redactor(redact)                             # same: stderr excerpts never carry a secret
    handle_detailed, handle = make_handler(modes, redact)
    bus = events.EventBus()
    outbox.add_listener(events.outbox_listener(bus))
    schedules(modes, bus).start()
    events.OutboxPoller(modes, bus).start()
    if not args.no_channels:
        channels.start_slack(modes["work"], handle)
        channels.start_telegram(modes["personal"], handle)
    token = api.load_or_create_token()
    api.copy_token_to_windows(token)
    log.info("Jarvis up on http://%s:%d", settings["host"], settings["port"])
    api.serve(api.create_app(modes, handle_detailed, token, bus), settings)


if __name__ == "__main__":
    main()
