"""ASGI-Anwendung: Dashboard (Basic-Auth), JSON-API und MCP-Endpunkt unter einem geheimen Pfad.

Start: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import base64
import contextlib
import datetime as dt
import logging
import secrets
from pathlib import Path

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
    return RedirectResponse(strava.authorize_url())


async def strava_callback(request: Request):
    if request.query_params.get("error") or not request.query_params.get("code"):
        return PlainTextResponse("Strava-Autorisierung abgebrochen oder fehlgeschlagen.", status_code=400)
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
    if config.SEED_PLAN and not db.get_kv("demo", False):
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

async def _send_text(send, status: int, text: str, headers: list | None = None):
    body = text.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()),
                            *(headers or [])]})
    await send({"type": "http.response.body", "body": body})


def _basic_ok(scope) -> bool:
    auth = dict(scope["headers"]).get(b"authorization", b"").decode()
    if not auth.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(user, config.DASH_USER) and secrets.compare_digest(pw, config.DASH_PASSWORD)


MCP_PREFIX = f"/mcp/{config.MCP_SECRET}"


async def app(scope, receive, send):
    if scope["type"] != "http":
        return await web(scope, receive, send)          # lifespan
    path = scope["path"]
    if path == MCP_PREFIX or path == MCP_PREFIX + "/":
        scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        return await mcp_app(scope, receive, send)
    if path == "/healthz":
        return await _send_text(send, 200, "ok")
    if path.startswith("/mcp"):
        return await _send_text(send, 404, "Not found")
    if not _basic_ok(scope):
        return await _send_text(send, 401, "Anmeldung erforderlich",
                                [(b"www-authenticate", b'Basic realm="Training Dashboard", charset="UTF-8"')])
    return await web(scope, receive, send)
