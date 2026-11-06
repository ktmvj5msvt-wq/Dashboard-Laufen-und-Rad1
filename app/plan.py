"""Trainingsplan: Datenmodell, Abgleich mit tatsächlichen Aktivitäten, Bearbeitung (UI und MCP)."""
import datetime as dt
from contextlib import closing

from . import db
from .util import parse_date, today

SPORTS = {"run", "ride", "strength", "rest", "other"}
STATUSES = {"planned", "done", "skipped", "moved"}
FIELDS = ("date", "sport", "kind", "title", "description", "duration_min", "distance_km", "target", "status", "notes")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _clean(data: dict, partial: bool = False) -> dict:
    out = {}
    for k in FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k == "date":
            v = parse_date(v).isoformat()
        elif k == "sport" and v not in SPORTS:
            raise ValueError(f"sport muss einer von {sorted(SPORTS)} sein")
        elif k == "status" and v not in STATUSES:
            raise ValueError(f"status muss einer von {sorted(STATUSES)} sein")
        elif k in ("duration_min", "distance_km") and v not in (None, ""):
            v = float(v)
        elif k in ("duration_min", "distance_km"):
            v = None
        out[k] = v
    if not partial:
        for req in ("date", "sport", "title"):
            if not out.get(req):
                raise ValueError(f"Pflichtfeld fehlt: {req}")
    return out


def upsert_session(data: dict) -> dict:
    sid = data.get("id")
    if sid:
        fields = _clean(data, partial=True)
        if not db.one("SELECT id FROM plan_sessions WHERE id=?", (sid,)):
            raise ValueError(f"Einheit {sid} existiert nicht")
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
            db.run(f"UPDATE plan_sessions SET {sets} WHERE id=?", (*fields.values(), _now(), sid))
    else:
        fields = _clean(data)
        fields.setdefault("status", "planned")
        cols = list(fields) + ["updated_at"]
        db.run(f"INSERT INTO plan_sessions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
               (*fields.values(), _now()))
        sid = db.one("SELECT MAX(id) AS i FROM plan_sessions")["i"]
    return db.one("SELECT * FROM plan_sessions WHERE id=?", (sid,))


def delete_session(sid: int) -> bool:
    if not db.one("SELECT id FROM plan_sessions WHERE id=?", (sid,)):
        return False
    db.run("DELETE FROM plan_sessions WHERE id=?", (sid,))
    return True


def replace_range(date_from: str, date_to: str, sessions: list[dict]) -> dict:
    """Ersetzt alle Einheiten im Zeitraum (inklusive) atomar durch die neue Liste."""
    d0, d1 = parse_date(date_from), parse_date(date_to)
    if d1 < d0:
        raise ValueError("date_to liegt vor date_from")
    cleaned = []
    for s in sessions:
        c = _clean(s)
        if not d0 <= parse_date(c["date"]) <= d1:
            raise ValueError(f"Einheit {c['date']} liegt außerhalb von {date_from}..{date_to}")
        c.setdefault("status", "planned")
        cleaned.append(c)
    removed = db.one("SELECT COUNT(*) AS n FROM plan_sessions WHERE date BETWEEN ? AND ?", (d0.isoformat(), d1.isoformat()))["n"]
    with closing(db.conn()) as c:              # eine Transaktion: alles oder nichts (Fehler -> Rollback)
        c.execute("DELETE FROM plan_sessions WHERE date BETWEEN ? AND ?", (d0.isoformat(), d1.isoformat()))
        for f in cleaned:
            cols = list(f) + ["updated_at"]
            c.execute(f"INSERT INTO plan_sessions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      (*f.values(), _now()))
        c.commit()
    return {"removed": removed, "added": len(cleaned)}


def _actuals(d0: dt.date, d1: dt.date) -> dict:
    acts = db.rows("SELECT date, sport, distance_m, moving_s FROM activities WHERE duplicate=0 AND date BETWEEN ? AND ?",
                   (d0.isoformat(), d1.isoformat()))
    out: dict[tuple, dict] = {}
    for a in acts:
        k = (a["date"], a["sport"])
        o = out.setdefault(k, {"km": 0.0, "min": 0.0, "n": 0})
        o["km"] += (a["distance_m"] or 0) / 1000
        o["min"] += (a["moving_s"] or 0) / 60
        o["n"] += 1
    return out


def effective_status(sess: dict, actual: dict | None, ref_day: dt.date) -> str:
    if sess["status"] in ("done", "skipped", "moved"):
        return sess["status"]
    if sess["sport"] == "rest":
        return "rest"
    if actual and actual["n"]:
        ok_km = sess["distance_km"] and actual["km"] >= 0.6 * sess["distance_km"]
        ok_min = sess["duration_min"] and actual["min"] >= 0.6 * sess["duration_min"]
        if ok_km or ok_min or (not sess["distance_km"] and not sess["duration_min"]):
            return "done"
        return "partial"
    d = parse_date(sess["date"])
    if d < ref_day:
        return "missed"
    return "today" if d == ref_day else "planned"


def list_sessions(date_from: str, date_to: str) -> dict:
    d0, d1 = parse_date(date_from), parse_date(date_to)
    sessions = db.rows("SELECT * FROM plan_sessions WHERE date BETWEEN ? AND ? ORDER BY date, id",
                       (d0.isoformat(), d1.isoformat()))
    actual = _actuals(d0, d1)
    ref = today()
    weeks: dict[str, dict] = {}
    for s in sessions:
        a = actual.get((s["date"], s["sport"]))
        s["actual_km"] = round(a["km"], 1) if a else None
        s["actual_min"] = round(a["min"]) if a else None
        s["effective_status"] = effective_status(s, a, ref)
        wk = (parse_date(s["date"]) - dt.timedelta(days=parse_date(s["date"]).weekday())).isoformat()
        w = weeks.setdefault(wk, {"week": wk, "planned_run_km": 0.0, "actual_run_km": 0.0, "planned_min": 0.0,
                                  "actual_min": 0.0, "sessions": 0, "done": 0, "missed": 0})
        w["sessions"] += 1
        if s["sport"] != "rest":
            w["planned_min"] += s["duration_min"] or 0
        if s["sport"] == "run":
            w["planned_run_km"] += s["distance_km"] or 0
        if s["effective_status"] == "done":
            w["done"] += 1
        if s["effective_status"] == "missed":
            w["missed"] += 1
    # Ist-Werte je Woche direkt aus Aktivitäten (unabhängig davon, ob etwas geplant war)
    for (date, sport), a in actual.items():
        wk = (parse_date(date) - dt.timedelta(days=parse_date(date).weekday())).isoformat()
        if wk in weeks:
            weeks[wk]["actual_min"] += a["min"]
            if sport == "run":
                weeks[wk]["actual_run_km"] += a["km"]
    return {"today": ref.isoformat(), "sessions": sessions, "weeks": sorted(weeks.values(), key=lambda w: w["week"])}


def set_status(sid: int, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status muss einer von {sorted(STATUSES)} sein")
    return upsert_session({"id": sid, "status": status})
