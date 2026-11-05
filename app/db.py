"""SQLite-Zugriff, Schema und Einstellungen."""
import datetime as dt
import json
import sqlite3
from contextlib import closing

from .config import DB_PATH
from .util import parse_date, parse_duration

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS activities(
  id TEXT PRIMARY KEY, source TEXT, sport TEXT, strava_type TEXT,
  start_local TEXT, date TEXT, name TEXT,
  distance_m REAL, moving_s INTEGER, elev_m REAL,
  avg_hr REAL, max_hr REAL, avg_watts REAL, has_hr INTEGER DEFAULT 0,
  load REAL DEFAULT 0, duplicate INTEGER DEFAULT 0,
  streams_done INTEGER DEFAULT 0, detail_done INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_act_date ON activities(date);

CREATE TABLE IF NOT EXISTS streams(activity_id TEXT PRIMARY KEY, blob BLOB);

CREATE TABLE IF NOT EXISTS z2(
  activity_id TEXT PRIMARY KEY, date TEXT, sport TEXT,
  z2_seconds REAL, z2_hr REAL, z2_pace_s_km REAL, z2_watts REAL, ef REAL
);
CREATE INDEX IF NOT EXISTS idx_z2_date ON z2(date);

CREATE TABLE IF NOT EXISTS performances(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT, label TEXT, distance_m REAL, time_s REAL,
  source TEXT, activity_id TEXT DEFAULT '', note TEXT,
  UNIQUE(date, label, source, activity_id)
);

CREATE TABLE IF NOT EXISTS health_daily(
  date TEXT PRIMARY KEY,
  sleep_s REAL, sleep_score REAL, hrv REAL, hrv_weekly REAL, hrv_status TEXT,
  rhr REAL, bb_max REAL, bb_min REAL, stress_avg REAL,
  readiness REAL, readiness_level TEXT, vo2max REAL, steps REAL, weight_kg REAL
);

CREATE TABLE IF NOT EXISTS plan_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, sport TEXT NOT NULL, kind TEXT, title TEXT, description TEXT,
  duration_min REAL, distance_km REAL, target TEXT,
  status TEXT DEFAULT 'planned', notes TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_plan_date ON plan_sessions(date);

CREATE TABLE IF NOT EXISTS sync_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, ok INTEGER, message TEXT
);
"""


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    with closing(conn()) as c:
        c.executescript(SCHEMA)
        c.commit()


def rows(sql: str, args=()) -> list[dict]:
    with closing(conn()) as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    r = rows(sql, args)
    return r[0] if r else None


def run(sql: str, args=()) -> int:
    with closing(conn()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def run_many(sql: str, seq) -> None:
    with closing(conn()) as c:
        c.executemany(sql, list(seq))
        c.commit()


def get_kv(key: str, default=None):
    r = one("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def set_kv(key: str, value) -> None:
    run("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)))


def log_sync(source: str, ok: bool, message: str) -> None:
    run("INSERT INTO sync_log(ts,source,ok,message) VALUES(?,?,?,?)",
        (dt.datetime.now().isoformat(timespec="seconds"), source, int(ok), message[:500]))


# ---------------------------------------------------------------- Einstellungen

DEFAULT_SETTINGS = {
    "race_name": "Marathon",
    "race_date": "2027-04-04",
    "race_distance_m": 42195.0,
    "goal_time_s": 12000.0,          # 3:20:00 – Platzhalter, bitte anpassen
    "hr_z2_low": 137,                # Z2-Herzfrequenzband (bpm), Garmin/Strava-Zonen des Athleten
    "hr_z2_high": 149,
    "hr_rest": 50,                   # für die Belastungsberechnung (Karvonen)
    "lthr": 165,                     # Laktatschwellen-Herzfrequenz
    "ftp": 273,
    "riegel_exponent": 1.06,
    "perf_window_days": 240,         # Referenzleistungen, die für die Prognose zählen
    "scenario_factors": {"potential": 1.00, "realistic": 1.03, "conservative": 1.07},
}

_TIME_KEYS = {"goal_time_s"}
_DATE_KEYS = {"race_date"}


def get_settings() -> dict:
    stored = get_kv("settings", {})
    merged = {**DEFAULT_SETTINGS, **stored}
    merged["scenario_factors"] = {**DEFAULT_SETTINGS["scenario_factors"], **stored.get("scenario_factors", {})}
    return merged


def update_settings(patch: dict) -> dict:
    """Validiert und speichert Änderungen. Unbekannte Schlüssel werden abgelehnt."""
    unknown = set(patch) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Unbekannte Einstellungen: {sorted(unknown)}. Erlaubt: {sorted(DEFAULT_SETTINGS)}")
    stored = get_kv("settings", {})
    for key, value in patch.items():
        if key in _TIME_KEYS:
            stored[key] = parse_duration(value)
        elif key in _DATE_KEYS:
            stored[key] = parse_date(value).isoformat()
        elif key == "scenario_factors":
            if not isinstance(value, dict):
                raise ValueError("scenario_factors muss ein Objekt sein")
            current = {**DEFAULT_SETTINGS["scenario_factors"], **stored.get("scenario_factors", {})}
            for name, factor in value.items():
                if name not in DEFAULT_SETTINGS["scenario_factors"]:
                    raise ValueError(f"Unbekanntes Szenario: {name}")
                current[name] = float(factor)
            stored[key] = current
        elif isinstance(DEFAULT_SETTINGS[key], str):
            stored[key] = str(value)
        else:
            stored[key] = float(value) if isinstance(DEFAULT_SETTINGS[key], float) else int(value)
    set_kv("settings", stored)
    return get_settings()
