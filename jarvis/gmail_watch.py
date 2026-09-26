"""OFW Gmail watcher (AD10), personal daemon only. Read-only and metadata-only by construction.

    python -m jarvis.gmail_watch --auth [--profile aws]

--auth runs Google's installed-app OAuth flow for gmail.readonly on a loopback callback (127.0.0.1:8766,
forwarded by scripts/oauth-login.sh) and stores the token at ~/.jarvis/gmail_watch_token.json (0600). The
client secrets JSON comes from GMAIL_WATCH_CLIENT_SECRETS (the personal env file) and exists on disk only
in a 0600 temp file for the flow.

At runtime a daemon thread lists message ids matching `ofw_watch.query` every `ofw_watch.poll_minutes`. The
list call requests `messages/id` only: no bodies, no snippets, no headers. For each new id one get() with
format=minimal and fields=id,internalDate reads the arrival time (AD39). A poll with new ids runs one
`/ofw-notify <since>` vault command, `since` being the earliest new internalDate minus 15 minutes (now minus
15 minutes when none could be read), then checks the OFW breaker (ofw_breaker). Gmail is never modified;
dedup is the local ~/.jarvis/ofw_watch_seen.json. Errors log event=ofw_watch_error with error_class only.
"""
from __future__ import annotations
import argparse, datetime as dt, json, logging, math, os, sys, tempfile, threading, time
from pathlib import Path
from typing import Callable
from . import brain, ledger, ofw_breaker, ofw_control, paths
from .logsetup import log_event

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = "gmail_watch_token.json"
SEEN_FILE = "ofw_watch_seen.json"
CLIENT_SECRETS_KEY = "GMAIL_WATCH_CLIENT_SECRETS"
AUTH_HOST, AUTH_BIND, AUTH_PORT = "localhost", "127.0.0.1", 8766
DEFAULT_POLL_MINUTES = 5
DEFAULT_QUERY = "from:@ourfamilywizard.com is:unread newer_than:2d"
LIST_FIELDS = "messages/id"
MAX_RESULTS = 100
SEEN_RETENTION_SECONDS = 14 * 24 * 3600
GET_FORMAT, GET_FIELDS = "minimal", "id,internalDate"
OFW_NOTIFY = "/ofw-notify"
NOTIFY_LOOKBACK_MINUTES = 15
MS_PER_SECOND = 1000
WATCH_MODE = "personal"


def token_path() -> Path:
    return paths.jarvis_dir() / TOKEN_FILE


def build_service(creds):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def load_service(path: Path):
    """The Gmail service from the stored token, or None when there is no token."""
    if not path.is_file():
        return None
    from google.oauth2.credentials import Credentials
    return build_service(Credentials.from_authorized_user_file(str(path), SCOPES))


def list_ids(service, query: str) -> list[str]:
    resp = service.users().messages().list(userId="me", q=query, maxResults=MAX_RESULTS,
                                           fields=LIST_FIELDS).execute() or {}
    return [str(m["id"]) for m in resp.get("messages", []) if isinstance(m, dict) and m.get("id")]


def internal_date(service, msg_id: str) -> float | None:
    """The message's arrival time (epoch seconds) from id and internalDate only, or None if unusable."""
    resp = service.users().messages().get(userId="me", id=msg_id, format=GET_FORMAT,
                                          fields=GET_FIELDS).execute() or {}
    try:
        seconds = int(str(resp.get("internalDate"))) / MS_PER_SECOND
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if seconds > 0 and math.isfinite(seconds) else None


def since_iso(earliest: float) -> str:
    """ISO 8601 UTC with offset, NOTIFY_LOOKBACK_MINUTES before earliest (for example 2026-09-25T14:03:00+00:00)."""
    at = dt.datetime.fromtimestamp(earliest, tz=dt.timezone.utc) - dt.timedelta(minutes=NOTIFY_LOOKBACK_MINUTES)
    return at.isoformat(timespec="seconds")


class Watcher(threading.Thread):
    def __init__(self, mode, cfg: dict | None, ask: Callable = None, service_factory: Callable | None = None,
                 clock: Callable[[], float] = time.time, status: Callable | None = None,
                 notify: Callable[[str], bool] | None = None):
        super().__init__(daemon=True, name="ofw-watch")
        cfg = cfg or {}
        self.mode, self.clock = mode, clock
        self.interval = float(cfg.get("poll_minutes") or DEFAULT_POLL_MINUTES) * 60
        self.query = str(cfg.get("query") or DEFAULT_QUERY)
        self._ask = ask or brain.ask_detailed
        self._factory = service_factory or (lambda: load_service(token_path()))
        self._service = None
        self._breaker = ofw_breaker.BreakerNotifier(status or (lambda m: ofw_control.status(m)), notify)
        self._halt = threading.Event()

    # ---- seen ids: {id: first_seen_epoch} ----
    def _seen_path(self) -> Path:
        d = paths.jarvis_dir()
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        return d / SEEN_FILE

    def _load_seen(self) -> dict:
        try:
            data = json.loads(self._seen_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log_event(log, "ofw_watch_seen_reset", logging.WARNING)
            return {}
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _save_seen(self, seen: dict) -> None:
        cutoff = self.clock() - SEEN_RETENTION_SECONDS
        kept = {k: v for k, v in seen.items() if v >= cutoff}
        ledger.atomic_write_text(self._seen_path(), json.dumps(kept, sort_keys=True))

    # ---- polling ----
    def poll_once(self) -> int:
        """One poll. Returns the number of new ids. Gmail and model errors are logged here; local state
        errors propagate to run(), which logs them and keeps polling."""
        try:
            if self._service is None:
                self._service = self._factory()
            ids = list_ids(self._service, self.query)
        except Exception as e:
            log_event(log, "ofw_watch_error", logging.ERROR, error_class=type(e).__name__)
            self._retry_breaker()
            return 0
        seen = self._load_seen()
        new = [i for i in ids if i not in seen]
        if not new:
            self._retry_breaker()
            return 0
        now = self.clock()
        self._save_seen({**seen, **{i: now for i in new}})       # seen first: a failing notify never loops
        self._notify(len(new), self._since(new, now))
        self._breaker.check(self.mode)
        return len(new)

    def _retry_breaker(self) -> None:
        """A breaker notice whose push failed is retried every poll, so a Telegram outage delays it by one
        poll interval rather than until the next OFW email."""
        if self._breaker.pending:
            self._breaker.check(self.mode)

    def _since(self, new: list[str], now: float) -> str:
        """Earliest readable internalDate of the new ids; a failed get() for one id only drops that id."""
        dates = []
        for msg_id in new:
            try:
                date = internal_date(self._service, msg_id)
            except Exception as e:
                log_event(log, "ofw_watch_error", logging.ERROR, error_class=type(e).__name__)
                continue
            if date is not None:
                dates.append(date)
        return since_iso(min(dates) if dates else now)

    def _notify(self, count: int, since: str) -> None:
        started = time.monotonic()
        try:
            ok = self._ask(self.mode, f"{OFW_NOTIFY} {since}").ok
        except Exception as e:
            log_event(log, "ofw_watch_error", logging.ERROR, error_class=type(e).__name__)
            return
        if not ok:
            log_event(log, "ofw_watch_error", logging.ERROR, error_class="OfwNotifyFailed")
            return
        log_event(log, "ofw_watch_notify", count=count, duration_ms=int((time.monotonic() - started) * 1000))

    def run(self) -> None:
        if not token_path().is_file():
            log_event(log, "ofw_watch_disabled", logging.WARNING, error_class="NoToken")
            return
        log_event(log, "ofw_watch_started", poll_minutes=self.interval / 60)
        while True:
            try:
                self.poll_once()
            except Exception as e:                        # a bad seen file or unwritable ~/.jarvis never kills the loop
                log_event(log, "ofw_watch_error", logging.ERROR, error_class=type(e).__name__)
            if self._halt.wait(self.interval):
                return

    def stop(self) -> None:
        self._halt.set()


# ---- --auth ----

def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ledger.atomic_write_text(path, text)                      # mkstemp: 0600, then atomic rename
    os.chmod(path, 0o600)


def authorize(client_secrets: str, token: Path, flow_cls=None, build_service=build_service) -> str:
    """Run the OAuth flow, store the token (0600), and return the connected account's address."""
    if flow_cls is None:
        from google_auth_oauthlib.flow import InstalledAppFlow as flow_cls
    fd, tmp = tempfile.mkstemp(prefix="gmail-client-", suffix=".json")   # 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(client_secrets)
        flow = flow_cls.from_client_secrets_file(tmp, scopes=SCOPES)
        creds = flow.run_local_server(host=AUTH_HOST, bind_addr=AUTH_BIND, port=AUTH_PORT, open_browser=False)
    finally:
        Path(tmp).unlink(missing_ok=True)
    _write_private(token, creds.to_json())
    service = build_service(creds)
    return str(service.users().getProfile(userId="me").execute().get("emailAddress", "")) if service else ""


def _client_secrets(profile: str | None) -> str | None:
    """From the process env, else the personal env file (loaded through the normal per-mode loader)."""
    if os.environ.get(CLIENT_SECRETS_KEY):
        return os.environ[CLIENT_SECRETS_KEY]
    from .modes import load_mode
    return load_mode(WATCH_MODE, profile=profile).env.get(CLIENT_SECRETS_KEY) or None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gmail_watch", description="OFW Gmail watcher (read-only)")
    ap.add_argument("--auth", action="store_true", help="authorize gmail.readonly and store the token")
    ap.add_argument("--profile", help="deployment profile override (default: JARVIS_DEPLOYMENT)")
    args = ap.parse_args(argv)
    if not args.auth:
        ap.error("nothing to do: the watcher runs inside the personal daemon; use --auth to authorize")
    secrets = _client_secrets(args.profile)
    if not secrets:
        raise SystemExit(f"{CLIENT_SECRETS_KEY} is not set in the personal env file")
    email = authorize(secrets, token_path())
    print(f"Gmail watcher authorized for: {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
