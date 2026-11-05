"""Konfiguration aus Umgebungsvariablen. Geheimnisse werden bei Bedarf erzeugt und in DATA_DIR abgelegt."""
import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger("training")

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "training.db"

# Öffentliche Adresse des Servers (für den Strava-OAuth-Redirect und die Anzeige der Konnektor-URL)
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD", "")
GARMIN_TOKEN_DIR = DATA_DIR / "garmin_tokens"

SYNC_INTERVAL_MIN = int(os.getenv("SYNC_INTERVAL_MIN", "60"))   # 0 = kein Hintergrund-Sync
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "450"))            # Wie weit der erste Strava-Sync zurückgeht
STREAM_BUDGET = int(os.getenv("STREAM_BUDGET", "20"))           # Aktivitäten mit Stream-Abruf je Sync (Strava-Rate-Limit)
GARMIN_DAYS = int(os.getenv("GARMIN_DAYS", "14"))               # Tage rückwirkend je Sync
SEED_PLAN = os.getenv("SEED_PLAN", "1") == "1"                  # Beispielplan (Nov/Dez 2026) beim ersten Start anlegen


def _persisted_secret(env_name: str, filename: str, nbytes: int) -> str:
    value = os.getenv(env_name)
    if value:
        return value
    path = DATA_DIR / filename
    if path.exists():
        return path.read_text().strip()
    value = secrets.token_urlsafe(nbytes)
    path.write_text(value)
    path.chmod(0o600)
    log.warning("%s nicht gesetzt: neues Geheimnis erzeugt und in %s gespeichert", env_name, path)
    return value


MCP_SECRET = _persisted_secret("MCP_SECRET", "mcp_secret", 24)
DASH_USER = os.getenv("DASH_USER", "trainer")
DASH_PASSWORD = _persisted_secret("DASH_PASSWORD", "dash_password", 12)
