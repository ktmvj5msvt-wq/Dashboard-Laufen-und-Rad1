"""Strava: OAuth, Aktivitäten, Streams (für Z2-Auswertung) und Bestleistungen."""
import datetime as dt
import time
from urllib.parse import urlencode

import requests

from . import analysis, config, db
from .util import parse_date, today

API = "https://www.strava.com/api/v3"
OAUTH = "https://www.strava.com/oauth"
RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
RIDE_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide"}
STRENGTH_TYPES = {"WeightTraining", "Workout", "Crossfit"}
EFFORT_LABELS = {5000: "5k", 10000: "10k", 21097: "Halbmarathon", 21098: "Halbmarathon", 42195: "Marathon", 42196: "Marathon"}


class RateLimited(Exception):
    pass


class NotConnected(Exception):
    pass


def configured() -> bool:
    return bool(config.STRAVA_CLIENT_ID and config.STRAVA_CLIENT_SECRET)


def connected() -> bool:
    return db.get_kv("strava_token") is not None


def sport_of(strava_type: str) -> str:
    if strava_type in RUN_TYPES:
        return "run"
    if strava_type in RIDE_TYPES:
        return "ride"
    if strava_type in STRENGTH_TYPES:
        return "strength"
    return "other"


# ---------------------------------------------------------------- OAuth

def authorize_url(state: str) -> str:
    q = {"client_id": config.STRAVA_CLIENT_ID, "response_type": "code", "approval_prompt": "auto", "state": state,
         "redirect_uri": f"{config.BASE_URL}/auth/strava/callback", "scope": "read,activity:read_all,profile:read_all"}
    return f"{OAUTH}/authorize?{urlencode(q)}"


def _store_token(tok: dict) -> None:
    old = db.get_kv("strava_token", {})
    db.set_kv("strava_token", {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
                               "expires_at": tok["expires_at"], "athlete": tok.get("athlete") or old.get("athlete")})


def exchange_code(code: str) -> None:
    r = requests.post(f"{OAUTH}/token", timeout=30, data={
        "client_id": config.STRAVA_CLIENT_ID, "client_secret": config.STRAVA_CLIENT_SECRET,
        "code": code, "grant_type": "authorization_code"})
    r.raise_for_status()
    tok = r.json()
    ath = tok.get("athlete") or {}
    tok["athlete"] = {"id": ath.get("id"), "name": f"{ath.get('firstname', '')} {ath.get('lastname', '')}".strip()}
    _store_token(tok)


def _access_token() -> str:
    tok = db.get_kv("strava_token")
    if not tok:
        raise NotConnected("Strava ist nicht verbunden")
    if tok["expires_at"] < time.time() + 120:
        r = requests.post(f"{OAUTH}/token", timeout=30, data={
            "client_id": config.STRAVA_CLIENT_ID, "client_secret": config.STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        r.raise_for_status()
        _store_token(r.json())
        tok = db.get_kv("strava_token")
    return tok["access_token"]


def _get(path: str, params: dict | None = None):
    r = requests.get(f"{API}{path}", params=params, timeout=40,
                     headers={"Authorization": f"Bearer {_access_token()}"})
    if r.status_code == 429:
        raise RateLimited("Strava-Rate-Limit erreicht")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- Aktivitäten

def _upsert_activity(a: dict, s: dict) -> None:
    stype = a.get("sport_type") or a.get("type") or ""
    sport = sport_of(stype)
    start = (a.get("start_date_local") or "").rstrip("Z")
    moving = a.get("moving_time") or 0
    avg_hr, avg_w = a.get("average_heartrate"), a.get("average_watts")
    load = analysis.activity_load(sport, moving, avg_hr, avg_w, s)
    db.run(
        """INSERT INTO activities(id,source,sport,strava_type,start_local,date,name,distance_m,moving_s,elev_m,
                                  avg_hr,max_hr,avg_watts,has_hr,load)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, distance_m=excluded.distance_m, moving_s=excluded.moving_s,
             elev_m=excluded.elev_m, avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, avg_watts=excluded.avg_watts,
             has_hr=excluded.has_hr, load=excluded.load, sport=excluded.sport, strava_type=excluded.strava_type""",
        (str(a["id"]), "strava", sport, stype, start, start[:10], a.get("name"), a.get("distance"), moving,
         a.get("total_elevation_gain"), avg_hr, a.get("max_heartrate"), avg_w, int(bool(a.get("has_heartrate"))), load))


def sync_activities() -> int:
    s = db.get_settings()
    last = db.one("SELECT MAX(date) AS d FROM activities WHERE source='strava'")
    floor = today() - dt.timedelta(days=config.HISTORY_DAYS)
    since = max(floor, parse_date(last["d"]) - dt.timedelta(days=14)) if last and last["d"] else floor
    after = int(dt.datetime.combine(since, dt.time.min).timestamp())
    page = count = 0
    while True:
        page += 1
        batch = _get("/athlete/activities", {"after": after, "per_page": 100, "page": page}) or []
        for a in batch:
            _upsert_activity(a, s)
            count += 1
        if len(batch) < 100:
            break
    mark_duplicates()
    return count


def mark_duplicates() -> int:
    """Doppelt aufgezeichnete Einheiten (z. B. Rolle in MyWhoosh + Garmin) nur einmal zählen."""
    rows = db.rows("SELECT id, sport, start_local, moving_s FROM activities WHERE sport IN ('ride','run') ORDER BY start_local")
    db.run("UPDATE activities SET duplicate=0 WHERE duplicate=1")
    dups = set()
    for i, a in enumerate(rows):
        if a["id"] in dups:
            continue
        ta = dt.datetime.fromisoformat(a["start_local"])
        for b in rows[i + 1:]:
            tb = dt.datetime.fromisoformat(b["start_local"])
            if (tb - ta).total_seconds() > 600:
                break
            if b["sport"] == a["sport"] and a["moving_s"] and abs(a["moving_s"] - b["moving_s"]) <= 0.15 * a["moving_s"]:
                dups.add(b["id"])
    if dups:
        db.run_many("UPDATE activities SET duplicate=1 WHERE id=?", [(i,) for i in dups])
    return len(dups)


# ---------------------------------------------------------------- Streams und Bestleistungen

def _store_best_efforts(a: dict, detail: dict) -> None:
    for e in detail.get("best_efforts") or []:
        label = EFFORT_LABELS.get(int(round(e.get("distance", 0))))
        if label:
            db.run("INSERT OR IGNORE INTO performances(date,label,distance_m,time_s,source,activity_id,note) VALUES(?,?,?,?,?,?,?)",
                   (a["date"], label, float(e["distance"]), float(e["elapsed_time"]), "strava_best_effort", a["id"],
                    f"Bestwert innerhalb von „{a['name']}“"))


def sync_streams(budget: int | None = None) -> dict:
    """Streams (Z2) und Bestleistungen nachladen, neueste zuerst. Stoppt sauber beim Rate-Limit."""
    budget = config.STREAM_BUDGET if budget is None else budget
    s = db.get_settings()
    todo = db.rows("""SELECT * FROM activities WHERE source='strava' AND duplicate=0 AND sport IN ('run','ride')
                      AND ((streams_done=0 AND has_hr=1) OR (sport='run' AND detail_done=0 AND distance_m>=5000))
                      ORDER BY date DESC LIMIT ?""", (budget,))
    done = {"streams": 0, "details": 0, "rate_limited": False, "remaining": 0}
    cutoff = (today() - dt.timedelta(days=365)).isoformat()
    try:
        for a in todo:
            if not a["streams_done"] and a["has_hr"]:
                st = _get(f"/activities/{a['id']}/streams",
                          {"keys": "time,heartrate,velocity_smooth,watts", "key_by_type": "true"}) or {}
                if st.get("time") and st.get("heartrate"):
                    get = lambda k: (st.get(k) or {}).get("data") or []
                    db.run("INSERT OR REPLACE INTO streams(activity_id,blob) VALUES(?,?)",
                           (a["id"], analysis.pack_streams(get("time"), get("heartrate"), get("velocity_smooth"), get("watts"))))
                    analysis.compute_z2(a["id"], a["sport"], a["date"], s)
                db.run("UPDATE activities SET streams_done=1 WHERE id=?", (a["id"],))
                done["streams"] += 1
            if a["sport"] == "run" and not a["detail_done"] and (a["distance_m"] or 0) >= 5000:
                if a["date"] >= cutoff:
                    detail = _get(f"/activities/{a['id']}") or {}
                    _store_best_efforts(a, detail)
                    done["details"] += 1
                db.run("UPDATE activities SET detail_done=1 WHERE id=?", (a["id"],))
    except RateLimited:
        done["rate_limited"] = True
    done["remaining"] = db.one("""SELECT COUNT(*) AS n FROM activities WHERE source='strava' AND duplicate=0
                                  AND sport IN ('run','ride') AND streams_done=0 AND has_hr=1""")["n"]
    return done
