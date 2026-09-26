"""Jarvis daemon, one per mode (AD4): `python -m jarvis.main --mode work|personal`.

Work runs the Slack DM channel and the work schedules. Personal runs Telegram, the personal schedules, the
OFW Gmail watcher and the Telegram draft push. Both run the API for the tray client, the outbox poller and
the heartbeat. The daemon loads only its own mode: the other mode's env file is never opened.
"""
from __future__ import annotations
import argparse, logging
from apscheduler.schedulers.background import BackgroundScheduler
from . import api, brain, channels, events, gmail_watch, heartbeat, logsetup, outbox, telegram_push
from .config import ROOT, load_config
from .handler import make_handler
from .logsetup import log_event
from .modes import CFG, Mode, load_mode, make_redactor

log = logging.getLogger("jarvis")
MISFIRE_GRACE_SECONDS = 3600      # WSL sleeps with Windows; run a missed job late (once) rather than skip it
MODE_NAMES = ("work", "personal")
SHARED_SECTIONS = ("jev", "outbox")   # read through modes.CFG by jev/router/outbox, so a profile must not differ


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jarvis", description="Jarvis daemon for one mode (always headless)")
    p.add_argument("--mode", required=True, choices=MODE_NAMES, help="the one mode this daemon serves")
    p.add_argument("--profile", help="deployment profile override for dev (default: JARVIS_DEPLOYMENT, "
                                     "then config.yaml)")
    p.add_argument("--headless", action="store_true", help="accepted for explicitness; the daemon is always headless")
    p.add_argument("--no-channels", action="store_true", help="do not start Slack, Telegram, or the Telegram push")
    return p.parse_args(argv)


def run_job(mode: Mode, job: str, bus: events.EventBus) -> None:
    """One scheduled command; always publishes schedule_done (ok false on failure)."""
    try:
        ok = brain.ask_detailed(mode, f"/{job}").ok
    except Exception as e:
        ok = False
        log_event(log, "schedule_error", logging.ERROR, job=job, error_class=type(e).__name__)
    if not ok:
        log_event(log, "schedule_incomplete", logging.WARNING, job=job)
    bus.publish("schedule_done", {"mode": mode.name, "job": job, "ok": ok})


def schedules(modes: dict[str, Mode], bus: events.EventBus, cfg: dict = CFG) -> BackgroundScheduler:
    """Jobs for the modes this daemon holds only (a per-mode daemon passes a one-entry dict)."""
    s = BackgroundScheduler(job_defaults={"misfire_grace_time": MISFIRE_GRACE_SECONDS, "coalesce": True})
    for mode_name, jobs in (cfg.get("schedule") or {}).items():
        if mode_name not in modes:
            continue
        for job, at in jobs.items():
            h, m = at.split(":")
            s.add_job(run_job, "cron", args=(modes[mode_name], job, bus), hour=int(h), minute=int(m),
                      day_of_week="mon-fri" if mode_name == "work" else "*")
    return s


def check_shared_sections(cfg: dict, module_cfg: dict) -> None:
    """--profile changes what main loads, not what jev/outbox read at import; refuse a mismatch."""
    differ = [k for k in SHARED_SECTIONS if cfg.get(k) != module_cfg.get(k)]
    if differ:
        raise SystemExit(f"Refusing to start: profile changes {', '.join(differ)}; "
                         "set JARVIS_DEPLOYMENT instead of --profile")


def _start_watcher(mode: Mode, cfg: dict, notify=None) -> gmail_watch.Watcher:
    """notify: the Telegram push's content-free send_text, for the OFW breaker notice (AD39)."""
    w = gmail_watch.Watcher(mode, cfg, notify=notify)
    w.start()
    return w


def start_services(mode: Mode, cfg: dict, bus: events.EventBus, redact, handle, channels_on: bool) -> None:
    """Mode-specific services: Slack for work; Telegram, the draft push and the OFW watcher for personal."""
    if mode.name == "work":
        if channels_on:
            channels.start_slack(mode, handle)
        return
    push = None
    if channels_on:
        channels.start_telegram(mode, handle)
        push = telegram_push.start(mode, bus, redact)
    if cfg.get("ofw_watch") is not None:
        _start_watcher(mode, cfg["ofw_watch"], notify=push.send_text if push else None)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = load_config(ROOT, args.profile) if args.profile else CFG
    check_shared_sections(cfg, CFG)
    deployment = cfg["deployment"]
    logsetup.configure(args.mode, deployment=deployment)
    settings = api.api_settings(cfg, args.mode)            # C7/AD5: fail before anything starts
    mode = load_mode(args.mode, ROOT, cfg)
    modes = {mode.name: mode}
    redact = make_redactor(modes)
    for wire in (logsetup.set_redactor, outbox.set_redactor, brain.set_redactor):
        wire(redact)                                       # M3: nothing logged, written, or returned carries a secret
    handle_detailed, handle = make_handler(modes, redact)
    bus = events.EventBus()
    outbox.add_listener(events.outbox_listener(bus))
    schedules(modes, bus, cfg).start()
    events.OutboxPoller(modes, bus).start()
    heartbeat.Heartbeat(mode.name).start()
    start_services(mode, cfg, bus, redact, handle, channels_on=not args.no_channels)
    token = api.load_or_create_token()
    api.share_token(token, deployment)
    log_event(log, "startup", status="listening", host=settings.host, port=settings.port)
    api.serve(api.create_app(modes, handle_detailed, token, bus, redact, bind=settings, deployment=deployment),
              settings)


if __name__ == "__main__":
    main()
