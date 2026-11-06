"""ASGI-Anwendung: Dashboard (Basic-Auth), JSON-API und MCP-Endpunkt unter einem geheimen Pfad.

Start: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import base64
import contextlib
import datetime as dt
import logging
import secrets
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import analysis, config, db, plan, plan_seed, strava, sync
from .mcp_tools import mcp
from .util import today

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("training")
STATIC = Path(__file__).parent / "static"


def jr(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


async def threaded(fn, *args):
    return await asyncio.to_thread(fn, *args)


# ---------------------------------------------------------------- API

async def api_status(request: Request):
    s = await threaded(sync.status)
    s["settings"] = db.get_settings()
    s["mcp_url"] = f"{config.BASE_URL}/mcp/{config.MCP_SECRET}"
    s["today"] = today().isoformat()
    return jr(s)


async def api_health(request: Request):
    days = int(request.query_params.get("days", 90))
    return jr(await threaded(analysis.health_overview, max(7, min(days, 365))))


async def api_plan(request: Request):
    mon = today() - dt.timedelta(days=today().weekday())
    d0 = request.query_params.get("from") or (mon - dt.timedelta(weeks=4)).isoformat()
    d1 = request.query_params.get("to") or (mon + dt.timedelta(weeks=26)).isoformat()
    return jr(await threaded(plan.list_sessions, d0, d1))


async def api_plan_status(request: Request):
    body = await request.json()
    try:
        return jr(await threaded(plan.set_status, int(request.path_params["sid"]), body.get("status", "")))
    except ValueError as e:
        return jr({"error": str(e)}, 400)


async def api_forecast(request: Request):
    return jr(await threaded(analysis.forecast))


async def api_z2(request: Request):
    sport = request.query_params.get("sport", "run")
    if sport not in ("run", "ride"):
        return jr({"error": "sport muss run oder ride sein"}, 400)
    weeks = max(4, min(int(request.query_params.get("weeks", 26)), 104))
    return jr(await threaded(analysis.z2_series, sport, weeks))


async def api_weekly(request: Request):
    return jr(await threaded(analysis.weekly_summary, max(4, min(int(request.query_params.get("weeks", 26)), 104))))


async def api_sync(request: Request):
    source = request.query_params.get("source", "all")
    if source not in ("all", "strava", "garmin", "z2"):
        return jr({"error": "ungültige Quelle"}, 400)
    return jr(await threaded(sync.run_sync, source))


async def strava_login(request: Request):
    if not strava.configured():
        return PlainTextResponse("STRAVA_CLIENT_ID und STRAVA_CLIENT_SECRET sind nicht gesetzt.", status_code=400)
    state = secrets.token_urlsafe(16)
    db.set_kv("strava_oauth_state", {"state": state, "ts": time.time()})
    return RedirectResponse(strava.authorize_url(state))


async def strava_callback(request: Request):
    if request.query_params.get("error") or not request.query_params.get("code"):
        return PlainTextResponse("Strava-Autorisierung abgebrochen oder fehlgeschlagen.", status_code=400)
    saved = db.get_kv("strava_oauth_state") or {}
    db.set_kv("strava_oauth_state", {})                 # nur einmal verwendbar
    if not saved.get("state") or not secrets.compare_digest(str(saved["state"]).encode(), request.query_params.get("state", "").encode()) \
            or time.time() - saved.get("ts", 0) > 600:
        return PlainTextResponse("Ungültiger oder abgelaufener Anmeldevorgang. Bitte /auth/strava erneut öffnen.", status_code=400)
    await threaded(strava.exchange_code, request.query_params["code"])
    asyncio.create_task(threaded(sync.run_sync, "strava"))
    return RedirectResponse("/")


async def healthz(request: Request):
    return PlainTextResponse("ok")


async def index(request: Request):
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------- Lebenszyklus

mcp_app = mcp.streamable_http_app(
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))   # Schutz übernimmt das geheime Pfadsegment


async def _sync_loop():
    await asyncio.sleep(10)
    while True:
        try:
            await threaded(sync.run_sync, "all")
        except Exception:  # noqa: BLE001
            log.exception("Hintergrund-Sync fehlgeschlagen")
        await asyncio.sleep(max(5, config.SYNC_INTERVAL_MIN) * 60)


@contextlib.asynccontextmanager
async def lifespan(app):
    db.init()
    if config.SEED_PLAN:
        n = plan_seed.seed_if_empty()
        if n:
            log.info("Startplan mit %d Einheiten angelegt", n)
    task = asyncio.create_task(_sync_loop()) if config.SYNC_INTERVAL_MIN > 0 else None
    async with mcp_app.router.lifespan_context(mcp_app):
        log.info("Dashboard bereit. Die Konnektor-URL steht unten im Dashboard unter „Claude-Konnektor einrichten“.")
        yield
    if task:
        task.cancel()


web = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/api/status", api_status),
        Route("/api/health", api_health),
        Route("/api/plan", api_plan),
        Route("/api/plan/{sid:int}/status", api_plan_status, methods=["POST"]),
        Route("/api/forecast", api_forecast),
        Route("/api/z2", api_z2),
        Route("/api/weekly", api_weekly),
        Route("/api/sync", api_sync, methods=["POST"]),
        Route("/auth/strava", strava_login),
        Route("/auth/strava/callback", strava_callback),
        Mount("/static", StaticFiles(directory=STATIC)),
    ],
)


# ---------------------------------------------------------------- Zugriffsschutz und Weiche

MAX_FAILS, FAIL_WINDOW_S = 8, 600          # 8 falsche Anmeldungen je IP in 10 Minuten -> 10 Minuten Sperre
_fails: dict[str, deque] = {}

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                 b"script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
]


def _client_ip(scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def _lock_remaining(ip: str) -> int:
    """Sekunden bis zum Ende der Sperre (0 = nicht gesperrt)."""
    q = _fails.get(ip)
    if not q:
        return 0
    now = time.monotonic()
    while q and now - q[0] > FAIL_WINDOW_S:
        q.popleft()
    if not q:
        _fails.pop(ip, None)
        return 0
    return int(FAIL_WINDOW_S - (now - q[0])) + 1 if len(q) >= MAX_FAILS else 0


def _record_fail(ip: str) -> None:
    if len(_fails) > 5000:                 # Speicher begrenzen
        _fails.clear()
    _fails.setdefault(ip, deque()).append(time.monotonic())


def _eq(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())      # Bytes: auch Nicht-ASCII-Zeichen sind erlaubt


def _check_basic(scope) -> tuple[bool, bool]:
    """(Anmeldung korrekt, Anmeldeversuch vorhanden)"""
    auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
    if not auth.lower().startswith("basic "):
        return False, False
    try:
        user, _, pw = base64.b64decode(auth[6:], validate=True).decode("utf-8").partition(":")
    except Exception:  # noqa: BLE001
        return False, True
    user_ok, pw_ok = _eq(user, config.DASH_USER), _eq(pw, config.DASH_PASSWORD)    # beide immer auswerten
    return user_ok and pw_ok, True


def _is_mcp_path(path: str) -> bool:
    if not path.startswith("/mcp/"):
        return False
    return _eq(path[5:].rstrip("/"), config.MCP_SECRET)


def _same_origin(scope) -> bool:
    """Schreibende Browser-Anfragen müssen von der eigenen Seite kommen (Schutz vor CSRF bei Basic-Auth)."""
    if scope["method"] in ("GET", "HEAD", "OPTIONS"):
        return True
    headers = dict(scope["headers"])
    origin = headers.get(b"origin")
    if not origin:
        return True                        # Nicht-Browser-Clients senden keinen Origin
    return urlparse(origin.decode("latin-1")).netloc == headers.get(b"host", b"").decode("latin-1")


async def _send_text(send, status: int, text: str, headers: list | None = None):
    body = text.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()),
                            *SECURITY_HEADERS, *(headers or [])]})
    await send({"type": "http.response.body", "body": body})


def _with_security_headers(send, path: str):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            present = {k.lower() for k, _ in headers}
            headers += [h for h in SECURITY_HEADERS if h[0] not in present]
            if path.startswith(("/api", "/auth")) or path == "/":
                headers.append((b"cache-control", b"no-store"))
            message = {**message, "headers": headers}
        await send(message)
    return wrapped


async def app(scope, receive, send):
    if scope["type"] != "http":
        return await web(scope, receive, send)          # lifespan
    path = scope["path"]
    if _is_mcp_path(path):                               # Zugriff nur über das geheime Pfadsegment
        scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        return await mcp_app(scope, receive, send)
    if path == "/healthz":
        return await _send_text(send, 200, "ok")
    if path.startswith("/mcp"):
        return await _send_text(send, 404, "Not found")
    ip = _client_ip(scope)
    wait = _lock_remaining(ip)
    if wait:
        return await _send_text(send, 429, "Zu viele fehlgeschlagene Anmeldungen. Bitte später erneut versuchen.",
                                [(b"retry-after", str(wait).encode())])
    ok, attempted = _check_basic(scope)
    if not ok:
        if attempted:
            _record_fail(ip)
        return await _send_text(send, 401, "Anmeldung erforderlich",
                                [(b"www-authenticate", b'Basic realm="Training Dashboard", charset="UTF-8"')])
    _fails.pop(ip, None)
    if not _same_origin(scope):
        return await _send_text(send, 403, "Anfrage von fremder Herkunft abgelehnt")
    return await web(scope, receive, _with_security_headers(send, path))
