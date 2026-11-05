"""MCP-Server: Claude liest hier Trainingsdaten und bearbeitet Plan, Einstellungen und Referenzleistungen."""
import datetime as dt

from mcp.server.mcpserver import MCPServer

from . import analysis, db, plan, sync
from .util import fmt_duration, fmt_pace, parse_date, parse_duration, today

mcp = MCPServer(
    "Training Dashboard",
    instructions=(
        "Zugriff auf das persönliche Trainingsdashboard (Strava- und Garmin-Daten). "
        "Beginne mit get_overview. Den Trainingsplan änderst du mit upsert_plan_session (einzelne Einheit), "
        "replace_plan_range (ganze Wochen neu planen) oder set_session_status. Plane auf Basis von get_health "
        "(Erholung), get_z2_history (aktuelle aerobe Form) und get_forecast. Zeiten als 'h:mm:ss', Pace als 'm:ss' je km, "
        "Daten als YYYY-MM-DD. sport ∈ run|ride|strength|rest|other. Prognosen sind Heuristiken, keine Garantien."
    ),
)


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


@mcp.tool()
def get_overview() -> dict:
    """Gesamtüberblick: Rennen und Prognose, Form, Gesundheit (aktuell), Wochenumfänge, Plan dieser Woche, Sync-Status."""
    forecast = analysis.forecast()
    health = analysis.health_overview(30)
    mon = _monday(today())
    week_plan = plan.list_sessions(mon.isoformat(), (mon + dt.timedelta(days=6)).isoformat())
    return {
        "today": today().isoformat(),
        "settings": db.get_settings(),
        "forecast": {k: forecast[k] for k in ("available", "race", "goal", "goal_pace", "scenarios", "trend_adjustment_pct")},
        "form": health["form"],
        "health_latest": health["latest"],
        "health_baseline": health["baseline"],
        "weekly_volume": analysis.weekly_summary(6),
        "this_week_plan": week_plan["sessions"],
        "sync": sync.status(),
    }


@mcp.tool()
def get_plan(date_from: str | None = None, date_to: str | None = None) -> dict:
    """Trainingsplan mit Ist-Abgleich. Standard: diese Woche bis in drei Wochen. Status: done, partial, missed, today, planned, skipped, moved, rest."""
    d0 = parse_date(date_from) if date_from else _monday(today())
    d1 = parse_date(date_to) if date_to else d0 + dt.timedelta(days=27)
    return plan.list_sessions(d0.isoformat(), d1.isoformat())


@mcp.tool()
def upsert_plan_session(date: str | None = None, sport: str | None = None, title: str | None = None,
                        id: int | None = None, kind: str | None = None, description: str | None = None,
                        duration_min: float | None = None, distance_km: float | None = None,
                        target: str | None = None, status: str | None = None, notes: str | None = None) -> dict:
    """Legt eine Einheit an (ohne id: date, sport, title nötig) oder ändert sie (mit id; nur übergebene Felder).
    kind z. B. easy|long|quality|endurance|recovery|strength. target z. B. '5:50–6:15 /km, HF < 150' oder '160–185 W'."""
    args = dict(date=date, sport=sport, title=title, id=id, kind=kind, description=description, duration_min=duration_min,
                distance_km=distance_km, target=target, status=status, notes=notes)
    return plan.upsert_session({k: v for k, v in args.items() if v is not None})


@mcp.tool()
def delete_plan_session(id: int) -> dict:
    """Löscht eine geplante Einheit."""
    return {"deleted": plan.delete_session(id)}


@mcp.tool()
def set_session_status(id: int, status: str) -> dict:
    """Setzt den Status manuell: planned, done, skipped oder moved. (Ohne manuellen Status gleicht das Dashboard automatisch mit Strava ab.)"""
    return plan.set_status(id, status)


@mcp.tool()
def replace_plan_range(date_from: str, date_to: str, sessions: list[dict]) -> dict:
    """Ersetzt ALLE Einheiten im Zeitraum (inklusive) atomar durch 'sessions' (Felder wie bei upsert_plan_session, ohne id).
    Der Weg für Neuplanung ganzer Wochen. Rufe vorher get_plan auf, damit nichts Gewolltes verloren geht."""
    return plan.replace_range(date_from, date_to, sessions)


@mcp.tool()
def get_forecast() -> dict:
    """Marathon-Prognose (Riegel aus 5k/10k/Halbmarathon-Bestleistungen im Referenzfenster, drei Szenarien, gedämpfter Z2-Trend), Ziel-Abstand, bisherige Marathons."""
    return analysis.forecast()


@mcp.tool()
def get_z2_history(sport: str = "run", weeks: int = 26) -> dict:
    """Verlauf der Z2-Leistung: sport=run → Pace (s/km) im Z2-Herzfrequenzband, sport=ride → Watt. Einzelwerte, Wochenmittel, Trend in %/Woche (+ = besser), Fortschreibung bis zum Renntag."""
    if sport not in ("run", "ride"):
        raise ValueError("sport muss 'run' oder 'ride' sein")
    return analysis.z2_series(sport, max(4, min(weeks, 104)))


@mcp.tool()
def get_health(days: int = 30) -> dict:
    """Garmin-Gesundheitsdaten (Schlaf, HRV, Ruhepuls, Body Battery, Stress, Readiness, VO2max) mit Basiswerten sowie Fitness/Ermüdung/Form (CTL/ATL/TSB)."""
    return analysis.health_overview(max(7, min(days, 365)))


@mcp.tool()
def get_recent_activities(days: int = 14, sport: str | None = None) -> list[dict]:
    """Letzte Aktivitäten (ohne Dubletten) mit Distanz, Dauer, Herzfrequenz, Leistung und Belastung."""
    since = (today() - dt.timedelta(days=max(1, min(days, 365)))).isoformat()
    sql = ("SELECT a.date, a.start_local, a.sport, a.name, ROUND(a.distance_m/1000.0,2) AS km, "
           "ROUND(a.moving_s/60.0,1) AS minutes, a.avg_hr, a.avg_watts, ROUND(a.load,0) AS load, "
           "z.z2_pace_s_km, z.z2_watts FROM activities a LEFT JOIN z2 z ON z.activity_id=a.id "
           "WHERE a.duplicate=0 AND a.date>=?")
    args: list = [since]
    if sport:
        sql += " AND a.sport=?"
        args.append(sport)
    return db.rows(sql + " ORDER BY a.start_local DESC", args)


@mcp.tool()
def get_settings() -> dict:
    """Aktuelle Einstellungen (Renndatum/-distanz, Zielzeit, Z2-Band, LTHR, FTP, Riegel-Exponent, Szenariofaktoren)."""
    return db.get_settings()


@mcp.tool()
def update_settings(changes: dict) -> dict:
    """Ändert Einstellungen. Beispiele: {"race_date": "2027-04-04", "goal_time_s": "3:20:00", "hr_z2_low": 137,
    "hr_z2_high": 149, "scenario_factors": {"realistic": 1.03}}. Nach Änderung des Z2-Bands trigger_sync('z2') ausführen."""
    return db.update_settings(changes)


@mcp.tool()
def add_performance(date: str, distance_m: float, time: str, label: str | None = None, note: str | None = None) -> dict:
    """Trägt eine Wettkampf-/Testleistung als Referenz für die Prognose ein (z. B. 10 km in 42:30). label: 5k|10k|Halbmarathon|Marathon."""
    seconds = parse_duration(time)
    if label is None:
        label = next((k for k, v in analysis.BANDS.items() if abs(v - distance_m) <= 0.01 * v), "andere")
    db.run("INSERT OR IGNORE INTO performances(date,label,distance_m,time_s,source,activity_id,note) VALUES(?,?,?,?,?,?,?)",
           (parse_date(date).isoformat(), label, distance_m, seconds, "manual", "", note))
    return {"saved": True, "label": label, "time": fmt_duration(seconds), "pace": fmt_pace(seconds / (distance_m / 1000))}


@mcp.tool()
def trigger_sync(source: str = "all") -> dict:
    """Startet einen Datenabruf: 'all', 'strava', 'garmin' oder 'z2' (Z2-Werte aus gespeicherten Streams neu berechnen)."""
    if source not in ("all", "strava", "garmin", "z2"):
        raise ValueError("source muss all, strava, garmin oder z2 sein")
    return sync.run_sync(source)
