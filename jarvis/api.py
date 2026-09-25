"""Local HTTP API for the Windows tray client. Loopback only, bearer token, no CORS (C5-C7)."""
from __future__ import annotations
import asyncio, hmac, json, logging, os, secrets, stat, threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Literal
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import outbox, paths
from .events import EventBus
from .modes import Mode, make_redactor

log = logging.getLogger(__name__)
HOST = "127.0.0.1"                      # hardcoded (C7); config may set only the port
LOOPBACK = {"127.0.0.1", "localhost"}
DEFAULT_PORT = 8765
HEARTBEAT_SECONDS = 15.0
SSE_POLL_SECONDS = 1.0
GRACEFUL_SHUTDOWN_SECONDS = 5
MAX_TEXT = 4000
TOKEN_BYTES = 32
ModeName = Literal["work", "personal"]
HEX64 = r"^[0-9a-f]{64}$"


class UtteranceIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    mode: ModeName
    mode_confirmed: bool = False


class ApproveIn(BaseModel):
    mode: ModeName
    id: str = Field(pattern=outbox.ID_RE.pattern)
    body_sha256: str = Field(pattern=HEX64)
    readback_sha256: str = Field(pattern=HEX64)
    reconfirm: bool = False


# ---- settings and token ----

def api_settings(cfg: dict) -> dict:
    """Validated {host, port}. A non-loopback api.host is a startup error."""
    api = cfg.get("api") or {}
    host = api.get("host", HOST)
    if host not in LOOPBACK:
        raise SystemExit(f"Refusing to start: api.host {host!r} is not loopback; only the port is configurable")
    port = api.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise SystemExit(f"Refusing to start: invalid api.port {port!r}")
    return {"host": HOST, "port": port}


def _valid_token(t: str) -> bool:
    return len(t) == TOKEN_BYTES * 2 and all(c in "0123456789abcdef" for c in t)


def load_or_create_token(path: Path | None = None) -> str:
    """32 random bytes hex at ~/.jarvis/api_token, created 0600 with O_EXCL, reused if present."""
    path = path or paths.jarvis_dir() / "api_token"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = path.read_text(encoding="utf-8").strip()
        if not _valid_token(token):
            raise SystemExit(f"Refusing to start: {path} is not a valid token (delete it to regenerate)")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            log.warning("%s was group/world accessible; fixing to 0600", path)
            os.chmod(path, 0o600)
        return token
    token = secrets.token_hex(TOKEN_BYTES)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


def copy_token_to_windows(token: str, dest_dir: Path | None = None) -> Path | None:
    """Copy to %LOCALAPPDATA%\\Jarvis\\api_token (user-only profile ACL). Warns and continues on failure."""
    dest_dir = dest_dir or paths.jarvis_localappdata()
    if dest_dir is None:
        log.warning("Windows LOCALAPPDATA unresolvable; api_token not copied for the tray client")
        return None
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "api_token"
        dest.write_text(token + "\n", encoding="utf-8")
        return dest
    except OSError as e:
        log.warning("could not copy api_token to %s: %s", dest_dir, e)
        return None


# ---- views ----

def redact_obj(obj, redact: Callable[[str], str]):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v, redact) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, redact) for v in obj]
    return obj


def item_view(mode: Mode, meta: dict, body: str, path: Path) -> dict:
    readback = outbox.render_readback(meta)
    return {
        "id": str(meta.get("id")), "mode": mode.name, "status": meta.get("status"),
        "preview": body, "first_line": next((l.strip() for l in body.splitlines() if l.strip()), ""),
        "body_sha256": outbox.body_sha256(body), "readback": readback, "readback_sha256": outbox.sha256(readback),
        "obsidian_uri": paths.obsidian_uri(mode.vault, f"outbox/{path.name}"),
        "windows_path": paths.to_windows(path), "last_block": meta.get("last_block"),
        "tone_flagged": outbox.tone_flagged(mode, meta, body),
    }


def outbox_view(mode: Mode) -> list[dict]:
    """One bad item is logged and skipped, never a 500 (M4)."""
    out = []
    for meta, body, path in outbox.reviewable(mode):
        try:
            out.append(item_view(mode, meta, body, path))
        except Exception:
            log.warning("skipping unlistable outbox item %s/%s", mode.name, path.name)
    return out


def health_view(modes: dict[str, Mode]) -> dict:
    """C4: booleans and counts only."""
    return {"ok": True, "modes": {n: {"vault": m.vault.is_dir(), "mcp_config": m.mcp_config.is_file(),
                                      "pending": len(outbox.pending(m))} for n, m in modes.items()}}


def sse_format(event: dict, redact: Callable[[str], str]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(redact_obj(event['data'], redact))}\n\n"


async def sse_stream(bus: EventBus, redact, is_disconnected, exiting: threading.Event,
                     heartbeat: float = HEARTBEAT_SECONDS, poll: float = SSE_POLL_SECONDS):
    """Ends when the client disconnects or the server is exiting, so shutdown never waits on a stream."""
    q = bus.subscribe()
    loop = asyncio.get_running_loop()
    last = loop.time()
    try:
        while not exiting.is_set() and not await is_disconnected():
            try:
                yield sse_format(await asyncio.wait_for(q.get(), timeout=poll), redact)
                last = loop.time()
            except asyncio.TimeoutError:
                if loop.time() - last >= heartbeat:
                    last = loop.time()
                    yield ": heartbeat\n\n"
    finally:
        bus.unsubscribe(q)


# ---- app ----

def _auth_dependency(token: str):
    expected = token.encode("utf-8")

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip().encode("utf-8"), expected):
            client = request.client.host if request.client else "?"
            log.warning("auth failed from %s for %s", client, request.url.path)   # never log the token
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
    return require_token


def _approve(modes, req: ApproveIn, redact) -> dict:
    """approve + execute under the shared per-mode lock (outbox.mode_lock, also used by the text channels)."""
    try:
        out = outbox.approve_and_execute(modes[req.mode], req.id, req.reconfirm, body_sha256=req.body_sha256,
                                         readback_sha256=req.readback_sha256, strict_reconfirm=True)
    except outbox.NotFound:
        raise HTTPException(404, "no such item")
    except outbox.BadReconfirm as e:
        raise HTTPException(400, str(e))
    except outbox.Blocked as e:
        raise HTTPException(409, redact(str(e)))
    except Exception:                                       # D11: fail closed, details stay server-side
        log.exception("approve/execute failed for %s/%s", req.mode, req.id)
        return {"result": "internal error", "sent": False, "blocked": "internal error"}
    return {"result": redact(out.message), "sent": out.sent, "blocked": redact(out.reason) if out.reason else None}


def create_app(modes: dict[str, Mode], handle_detailed, token: str, bus: EventBus,
               redact: Callable[[str], str] | None = None) -> FastAPI:
    if not _valid_token(token):
        raise ValueError("invalid api token")
    redact = redact or make_redactor(modes)
    exiting = threading.Event()

    @asynccontextmanager
    async def lifespan(_app):
        bus.attach_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(_auth_dependency(token))])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(LOOPBACK))
    app.state.exiting = exiting

    @app.get("/health")
    def health() -> dict:
        return health_view(modes)

    @app.post("/utterance")
    def utterance(req: UtteranceIn) -> dict:
        reply = handle_detailed(req.text, "voice", voice_mode=req.mode, mode_confirmed=req.mode_confirmed)
        return {"reply": redact(reply.text), "mode_used": reply.mode, "needs_mode": reply.needs_mode}

    @app.get("/outbox")
    def list_outbox(mode: ModeName = Query(...)) -> dict:
        return {"items": redact_obj(outbox_view(modes[mode]), redact)}

    @app.post("/approve")
    def approve(req: ApproveIn) -> dict:
        return _approve(modes, req, redact)

    @app.get("/events")
    async def events(request: Request):
        return StreamingResponse(sse_stream(bus, redact, request.is_disconnected, exiting), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


class _Server(uvicorn.Server):
    """Tells open SSE streams to finish as soon as a stop signal arrives."""

    def __init__(self, config: uvicorn.Config, exiting: threading.Event):
        super().__init__(config)
        self._exiting = exiting

    def handle_exit(self, sig, frame) -> None:
        self._exiting.set()
        super().handle_exit(sig, frame)


def server_config(app: FastAPI, settings: dict) -> uvicorn.Config:
    if settings.get("host") != HOST:
        raise ValueError("the API binds 127.0.0.1 only")
    return uvicorn.Config(app, host=HOST, port=settings["port"], log_level="info", proxy_headers=False,
                          server_header=False, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS)


def serve(app: FastAPI, settings: dict) -> None:
    _Server(server_config(app, settings), app.state.exiting).run()
