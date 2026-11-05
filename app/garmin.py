"""Garmin-Gesundheitsdaten über die inoffizielle Bibliothek `garminconnect`.

Hinweise:
- Garmin bietet für Privatpersonen keine offizielle API. Die Bibliothek nutzt dieselben Endpunkte wie die Web-App
  und kann sich ohne Vorankündigung ändern. Jede Metrik wird deshalb einzeln und fehlertolerant abgefragt.
- Bei aktivierter Zwei-Faktor-Anmeldung einmalig `python -m app.garmin_login` ausführen. Danach genügen die
  gespeicherten Token in DATA_DIR/garmin_tokens.
"""
import datetime as dt

from . import config, db
from .util import today


def configured() -> bool:
    return bool(config.GARMIN_TOKEN_DIR.exists() or (config.GARMIN_EMAIL and config.GARMIN_PASSWORD))


def dig(obj, *path):
    """Sicher durch verschachtelte dicts/Listen navigieren."""
    for key in path:
        if obj is None:
            return None
        try:
            obj = obj[key]
        except (KeyError, IndexError, TypeError):
            return None
    return obj


def client(prompt_mfa=None):
    from garminconnect import Garmin  # erst hier importieren, damit der Rest ohne die Bibliothek läuft
    g = Garmin(config.GARMIN_EMAIL or None, config.GARMIN_PASSWORD or None, prompt_mfa=prompt_mfa) \
        if prompt_mfa else Garmin(config.GARMIN_EMAIL or None, config.GARMIN_PASSWORD or None)
    g.login(str(config.GARMIN_TOKEN_DIR))      # nutzt gespeicherte Token, sonst Anmeldung mit E-Mail/Passwort
    return g


def parse_day(g, day: str) -> tuple[dict, list[str]]:
    """Liest alle Metriken eines Tages; liefert (Werte, Fehlerliste)."""
    out: dict = {}
    errors: list[str] = []

    def attempt(name, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 – einzelne Metrik darf ausfallen
            errors.append(f"{name}: {type(e).__name__}")

    def sleep():
        d = g.get_sleep_data(day)
        out["sleep_s"] = dig(d, "dailySleepDTO", "sleepTimeSeconds")
        out["sleep_score"] = dig(d, "dailySleepDTO", "sleepScores", "overall", "value")
        if out.get("hrv") is None:
            out["hrv"] = d.get("avgOvernightHrv") if isinstance(d, dict) else None

    def hrv():
        d = g.get_hrv_data(day)
        out["hrv"] = dig(d, "hrvSummary", "lastNightAvg") or out.get("hrv")
        out["hrv_weekly"] = dig(d, "hrvSummary", "weeklyAvg")
        out["hrv_status"] = dig(d, "hrvSummary", "status")

    def rhr():
        d = g.get_rhr_day(day)
        out["rhr"] = dig(d, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE", 0, "value")

    def body_battery():
        d = g.get_body_battery(day, day)
        vals = [p[1] for p in (dig(d, 0, "bodyBatteryValuesArray") or []) if len(p) > 1 and p[1] is not None]
        if vals:
            out["bb_max"], out["bb_min"] = max(vals), min(vals)

    def stress():
        out["stress_avg"] = dig(g.get_stress_data(day), "avgStressLevel")

    def readiness():
        d = g.get_training_readiness(day)
        first = d[0] if isinstance(d, list) and d else d
        out["readiness"] = dig(first, "score")
        out["readiness_level"] = dig(first, "level")

    def vo2():
        d = g.get_max_metrics(day)
        first = d[0] if isinstance(d, list) and d else d
        out["vo2max"] = dig(first, "generic", "vo2MaxPreciseValue") or dig(first, "generic", "vo2MaxValue")

    def stats():
        d = g.get_stats(day)
        out["steps"] = dig(d, "totalSteps")
        if out.get("rhr") is None:
            out["rhr"] = dig(d, "restingHeartRate")

    def weight():
        d = g.get_body_composition(day, day)
        grams = dig(d, "totalAverage", "weight")
        if grams:
            out["weight_kg"] = grams / 1000

    for name, fn in (("sleep", sleep), ("hrv", hrv), ("rhr", rhr), ("body_battery", body_battery), ("stress", stress),
                     ("readiness", readiness), ("vo2max", vo2), ("stats", stats), ("weight", weight)):
        attempt(name, fn)
    return out, errors


COLS = ["sleep_s", "sleep_score", "hrv", "hrv_weekly", "hrv_status", "rhr", "bb_max", "bb_min", "stress_avg",
        "readiness", "readiness_level", "vo2max", "steps", "weight_kg"]


def save_day(day: str, values: dict) -> None:
    present = {k: values[k] for k in COLS if values.get(k) is not None}
    if not present:
        return
    cols = ["date"] + list(present)
    updates = ", ".join(f"{k}=excluded.{k}" for k in present)
    db.run(f"INSERT INTO health_daily({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
           f"ON CONFLICT(date) DO UPDATE SET {updates}", (day, *present.values()))


def sync_health(days: int | None = None) -> dict:
    g = client()
    days = days or config.GARMIN_DAYS
    first_run = db.one("SELECT COUNT(*) AS n FROM health_daily")["n"] == 0
    if first_run:
        days = max(days, 60)
    result = {"days": 0, "errors": 0}
    for i in range(days):
        day = (today() - dt.timedelta(days=i)).isoformat()
        values, errors = parse_day(g, day)
        save_day(day, values)
        result["days"] += 1
        result["errors"] += len(errors)
    return result
