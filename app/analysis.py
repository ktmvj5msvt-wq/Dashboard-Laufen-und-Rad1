"""Auswertungen: Z2-Verlauf, Belastung, Wochenumfänge, Gesundheit, Marathon-Prognose.

Alle Prognosen sind Heuristiken (Riegel-Formel + gedämpfter Z2-Trend), keine Garantien.
"""
import datetime as dt
import json
import statistics
import zlib

from . import db
from .util import fmt_duration, fmt_pace, parse_date, today

BANDS = {"5k": 5000.0, "10k": 10000.0, "Halbmarathon": 21097.5, "Marathon": 42195.0}


# ---------------------------------------------------------------- Hilfsfunktionen

def week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def ols(xs: list[float], ys: list[float]):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def activity_load(sport: str, moving_s: float, avg_hr, avg_watts, s: dict) -> float:
    """Heuristische Trainingsbelastung (TSS-ähnlich: 1 h an der Schwelle = 100)."""
    hours = (moving_s or 0) / 3600
    if avg_hr and s["lthr"] > s["hr_rest"]:
        ratio = (avg_hr - s["hr_rest"]) / (s["lthr"] - s["hr_rest"])
    elif avg_watts and s["ftp"] and sport == "ride":
        ratio = avg_watts / s["ftp"]
    else:
        return hours * 50
    ratio = clamp(ratio, 0.3, 1.3)
    return hours * ratio * ratio * 100


# ---------------------------------------------------------------- Z2

def z2_from_samples(sport: str, t, hr, v, w, lo: float, hi: float,
                    warmup_s: float = 300, min_seconds: float = 600):
    """Mittelwerte über alle Messpunkte mit Herzfrequenz im Z2-Band (ohne Einlaufphase und Stillstand)."""
    n = len(t)
    secs = dist = hr_sum = w_sum = w_secs = 0.0
    for i in range(n):
        if t[i] < warmup_s:
            continue
        h = hr[i] if i < len(hr) else None
        if h is None or h < lo or h > hi:
            continue
        dt_i = (t[i + 1] - t[i]) if i + 1 < n else 1
        dt_i = clamp(dt_i, 0, 15)
        if dt_i == 0:
            continue
        speed = v[i] if v and i < len(v) else None
        watts = w[i] if w and i < len(w) else None
        if sport == "run" and (speed is None or speed < 1.5):
            continue
        if sport == "ride" and (watts is None or watts < 40):
            continue
        secs += dt_i
        hr_sum += h * dt_i
        if speed is not None:
            dist += speed * dt_i
        if watts is not None and watts > 0:
            w_sum += watts * dt_i
            w_secs += dt_i
    if secs < min_seconds:
        return None
    avg_hr = hr_sum / secs
    out = {"z2_seconds": secs, "z2_hr": avg_hr, "z2_pace_s_km": None, "z2_watts": None, "ef": None}
    if sport == "run" and dist > 0:
        speed = dist / secs
        out["z2_pace_s_km"] = 1000 / speed
        out["ef"] = speed * 60 / avg_hr                      # m/min je Herzschlag
    if w_secs > 0:
        out["z2_watts"] = w_sum / w_secs
        if sport == "ride":
            out["ef"] = out["z2_watts"] / avg_hr             # W je Herzschlag
    return out


def pack_streams(t, hr, v, w, step_s: float = 5.0) -> bytes:
    """Streams auf ~5-s-Raster ausdünnen und komprimiert speichern (Z2-Band lässt sich später ohne API-Aufruf ändern)."""
    keep, last = [], None
    for i, ti in enumerate(t):
        if last is None or ti - last >= step_s:
            keep.append(i)
            last = ti
    pick = lambda arr: [arr[i] if arr and i < len(arr) else None for i in keep]
    data = {"t": pick(t), "hr": pick(hr), "v": pick(v), "w": pick(w)}
    return zlib.compress(json.dumps(data).encode())


def compute_z2(activity_id: str, sport: str, date: str, s: dict | None = None) -> bool:
    s = s or db.get_settings()
    row = db.one("SELECT blob FROM streams WHERE activity_id=?", (activity_id,))
    db.run("DELETE FROM z2 WHERE activity_id=?", (activity_id,))
    if not row:
        return False
    d = json.loads(zlib.decompress(row["blob"]))
    res = z2_from_samples(sport, d["t"], d["hr"], d["v"], d["w"], s["hr_z2_low"], s["hr_z2_high"])
    if not res:
        return False
    db.run("INSERT INTO z2(activity_id,date,sport,z2_seconds,z2_hr,z2_pace_s_km,z2_watts,ef) VALUES(?,?,?,?,?,?,?,?)",
           (activity_id, date, sport, res["z2_seconds"], res["z2_hr"], res["z2_pace_s_km"], res["z2_watts"], res["ef"]))
    return True


def recompute_all_z2() -> int:
    """Nach Änderung des Z2-Bands: aus gespeicherten Streams neu berechnen."""
    s = db.get_settings()
    n = 0
    for a in db.rows("SELECT a.id, a.sport, a.date FROM activities a JOIN streams st ON st.activity_id=a.id"):
        n += compute_z2(a["id"], a["sport"], a["date"], s)
    return n


def z2_series(sport: str, weeks: int = 26) -> dict:
    """Z2-Einzelwerte, Wochenmittel, Trend (% pro Woche, + = besser) und Fortschreibung bis zum Renntag."""
    s = db.get_settings()
    since = (today() - dt.timedelta(weeks=weeks)).isoformat()
    col = "z2_pace_s_km" if sport == "run" else "z2_watts"
    rows = db.rows(
        f"SELECT z.date, z.z2_seconds, z.z2_hr, z.{col} AS value, z.ef, a.name, a.id "
        f"FROM z2 z JOIN activities a ON a.id=z.activity_id "
        f"WHERE z.sport=? AND z.date>=? AND z.{col} IS NOT NULL AND a.duplicate=0 ORDER BY z.date", (sport, since))
    points = [{"date": r["date"], "value": r["value"], "hr": r["z2_hr"], "seconds": r["z2_seconds"],
               "ef": r["ef"], "name": r["name"]} for r in rows]
    # Wochenmittel, gewichtet nach Z2-Zeit; Pace über die mittlere Geschwindigkeit
    buckets: dict[dt.date, list] = {}
    for p in points:
        buckets.setdefault(week_start(parse_date(p["date"])), []).append(p)
    weekly = []
    for wk in sorted(buckets):
        grp = buckets[wk]
        tot = sum(p["seconds"] for p in grp)
        if sport == "run":
            speed = sum((1000 / p["value"]) * p["seconds"] for p in grp) / tot
            val = 1000 / speed
        else:
            val = sum(p["value"] * p["seconds"] for p in grp) / tot
        weekly.append({"date": wk.isoformat(), "value": val, "seconds": tot, "n": len(grp)})
    result = {"sport": sport, "unit": "s/km" if sport == "run" else "W", "hr_band": [s["hr_z2_low"], s["hr_z2_high"]],
              "points": points, "weekly": weekly, "trend": None, "projection": None}
    recent = weekly[-12:]
    if len(recent) >= 4:
        xs = [(parse_date(w["date"]) - dt.date(2000, 1, 1)).days / 7 for w in recent]
        ys = [(1000 / w["value"]) if sport == "run" else w["value"] for w in recent]   # Geschwindigkeit bzw. Watt: größer = besser
        fit = ols(xs, ys)
        if fit:
            slope, icpt = fit
            mean = sum(ys) / len(ys)
            result["trend"] = {"pct_per_week": slope / mean * 100, "weeks_used": len(recent),
                               "change_pct_over_window": slope * (xs[-1] - xs[0]) / mean * 100}
            race = parse_date(s["race_date"])
            if race > today():
                x_race = (race - dt.date(2000, 1, 1)).days / 7
                y_race = icpt + slope * x_race
                if y_race > 0:
                    to_val = (lambda y: 1000 / y) if sport == "run" else (lambda y: y)
                    result["projection"] = [
                        {"date": recent[0]["date"], "value": to_val(icpt + slope * xs[0])},
                        {"date": race.isoformat(), "value": to_val(y_race)},
                    ]
    return result


# ---------------------------------------------------------------- Umfang & Belastung

def _activities_since(since: dt.date, sport: str | None = None) -> list[dict]:
    sql = "SELECT * FROM activities WHERE duplicate=0 AND date>=?"
    args = [since.isoformat()]
    if sport:
        sql += " AND sport=?"
        args.append(sport)
    return db.rows(sql + " ORDER BY date", args)


def weekly_summary(weeks: int = 16) -> list[dict]:
    start = week_start(today()) - dt.timedelta(weeks=weeks - 1)
    out = {start + dt.timedelta(weeks=i): {"week": (start + dt.timedelta(weeks=i)).isoformat(), "run_km": 0.0,
           "run_h": 0.0, "ride_km": 0.0, "ride_h": 0.0, "strength": 0, "load": 0.0, "longest_run_km": 0.0}
           for i in range(weeks)}
    for a in _activities_since(start):
        b = out.get(week_start(parse_date(a["date"])))
        if not b:
            continue
        km, h = (a["distance_m"] or 0) / 1000, (a["moving_s"] or 0) / 3600
        b["load"] += a["load"] or 0
        if a["sport"] == "run":
            b["run_km"] += km
            b["run_h"] += h
            b["longest_run_km"] = max(b["longest_run_km"], km)
        elif a["sport"] == "ride":
            b["ride_km"] += km
            b["ride_h"] += h
        elif a["sport"] == "strength":
            b["strength"] += 1
    return list(out.values())


def load_curve(days: int = 120) -> list[dict]:
    """Tägliche Belastung mit Fitness (CTL, 42 Tage), Ermüdung (ATL, 7 Tage) und Form (TSB = CTL - ATL)."""
    end = today()
    first = end - dt.timedelta(days=days + 84)          # 12 Wochen Einschwingzeit
    daily: dict[str, float] = {}
    for a in _activities_since(first):
        daily[a["date"]] = daily.get(a["date"], 0) + (a["load"] or 0)
    ctl = atl = 0.0
    out = []
    d = first
    while d <= end:
        load = daily.get(d.isoformat(), 0.0)
        ctl += (load - ctl) / 42
        atl += (load - atl) / 7
        if d >= end - dt.timedelta(days=days):
            out.append({"date": d.isoformat(), "load": load, "ctl": ctl, "atl": atl, "tsb": ctl - atl})
        d += dt.timedelta(days=1)
    return out


def form_label(tsb: float) -> str:
    if tsb > 10:
        return "frisch"
    if tsb >= -10:
        return "ausgewogen"
    if tsb >= -30:
        return "produktiv ermüdet"
    return "stark ermüdet"


# ---------------------------------------------------------------- Gesundheit

HEALTH_METRICS = ["sleep_s", "sleep_score", "hrv", "hrv_weekly", "hrv_status", "rhr", "bb_max", "bb_min",
                  "stress_avg", "readiness", "readiness_level", "vo2max", "steps", "weight_kg"]


def health_overview(days: int = 90) -> dict:
    since = (today() - dt.timedelta(days=days)).isoformat()
    series = db.rows("SELECT * FROM health_daily WHERE date>=? ORDER BY date", (since,))
    latest = {}
    for m in HEALTH_METRICS:
        r = db.one(f"SELECT date, {m} AS v FROM health_daily WHERE {m} IS NOT NULL ORDER BY date DESC LIMIT 1")
        if r:
            latest[m] = {"value": r["v"], "date": r["date"]}

    def avg(metric: str, n: int, skip_latest: bool = False):
        vals = [r[metric] for r in series if r.get(metric) is not None][-(n + (1 if skip_latest else 0)):]
        if skip_latest:
            vals = vals[:-1]
        return statistics.fmean(vals) if vals else None

    curve = load_curve(days)
    form = None
    if curve:
        last = curve[-1]
        form = {"ctl": last["ctl"], "atl": last["atl"], "tsb": last["tsb"], "label": form_label(last["tsb"])}
    return {"latest": latest,
            "baseline": {"hrv_7d": avg("hrv", 7), "hrv_28d": avg("hrv", 28), "rhr_7d": avg("rhr", 7),
                         "rhr_28d": avg("rhr", 28), "sleep_7d": avg("sleep_s", 7)},
            "series": series, "load": curve, "form": form, "weekly": weekly_summary(12)}


# ---------------------------------------------------------------- Prognose

def forecast() -> dict:
    s = db.get_settings()
    race, dist, exp = parse_date(s["race_date"]), s["race_distance_m"], s["riegel_exponent"]
    weeks_left = max(0, (race - today()).days) / 7
    since = (today() - dt.timedelta(days=s["perf_window_days"])).isoformat()
    perfs = db.rows("SELECT * FROM performances WHERE date>=? AND label IN ('5k','10k','Halbmarathon') ORDER BY date", (since,))
    best: dict[str, dict] = {}
    for p in perfs:
        if p["label"] not in best or p["time_s"] < best[p["label"]]["time_s"]:
            best[p["label"]] = p
    marathons = db.rows("SELECT date, time_s, source, note FROM performances WHERE label='Marathon' ORDER BY date DESC LIMIT 6")
    base = {"available": False, "race": {"name": s["race_name"], "date": s["race_date"], "distance_m": dist,
            "weeks_left": round(weeks_left, 1)}, "goal_time_s": s["goal_time_s"], "goal": fmt_duration(s["goal_time_s"]),
            "goal_pace": fmt_pace(s["goal_time_s"] / (dist / 1000)),
            "marathons": [{**m, "time": fmt_duration(m["time_s"])} for m in marathons],
            "references": [], "scenarios": [], "trend_adjustment_pct": 0.0, "factors": s["scenario_factors"]}
    if not best:
        return base
    refs = []
    for label, p in best.items():
        pred = p["time_s"] * (dist / p["distance_m"]) ** exp
        refs.append({"label": label, "date": p["date"], "time_s": p["time_s"], "time": fmt_duration(p["time_s"]),
                     "source": p["source"], "predicted_s": pred, "predicted": fmt_duration(pred),
                     "predicted_pace": fmt_pace(pred / (dist / 1000))})
    preds = [r["predicted_s"] for r in refs]
    potential, basis = min(preds), statistics.median(preds)
    trend = z2_series("run", 26)["trend"]
    adj = 0.0
    if trend:
        adj = clamp(trend["pct_per_week"] / 100 * weeks_left * 0.5, -0.05, 0.05)   # 50 % gedämpft, max ±5 %
    scenarios = []
    for name, factor in s["scenario_factors"].items():
        t0 = (potential if name == "potential" else basis) * factor
        t1 = t0 * (1 - adj)
        scenarios.append({"name": name, "factor": factor, "time_s": t0, "time": fmt_duration(t0),
                          "pace": fmt_pace(t0 / (dist / 1000)), "time_trend_s": t1, "time_trend": fmt_duration(t1),
                          "pace_trend": fmt_pace(t1 / (dist / 1000)), "vs_goal_s": t1 - s["goal_time_s"]})
    base.update(available=True, references=refs, scenarios=scenarios, trend_adjustment_pct=adj * 100,
                z2_trend=trend)
    return base
