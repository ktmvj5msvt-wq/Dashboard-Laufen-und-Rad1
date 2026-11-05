"""Erzeugt SYNTHETISCHE Demodaten (nur zum Ausprobieren der Oberfläche, nicht für den Echtbetrieb).

    DATA_DIR=./demo-data python scripts/seed_demo.py

Setzt ein Demo-Flag; das Dashboard zeigt dann einen deutlichen Hinweis. Mit echten Strava-/Garmin-Daten nicht verwenden.
"""
import datetime as dt
import math
import random

from app import analysis, db, plan_seed
from app.util import today

random.seed(7)


def main() -> None:
    db.init()
    db.set_kv("demo", True)
    s = db.get_settings()
    end = today()
    start = end - dt.timedelta(weeks=26)
    n = 0
    d = start
    while d <= end:
        weeks_in = (d - start).days / 7
        dow = d.weekday()
        pace_z2 = 395 - 1.1 * weeks_in + random.uniform(-6, 6)          # Z2-Pace verbessert sich langsam
        watts_z2 = 150 + 0.55 * weeks_in + random.uniform(-4, 4)
        sessions = []
        if dow in (0, 2, 4):
            sessions.append(("run", random.choice([8, 10, 12]), pace_z2))
        if dow == 6:
            sessions.append(("run", random.choice([16, 18, 20]), pace_z2 + 8))
        if dow in (1, 3):
            sessions.append(("ride", random.choice([60, 75, 90]), watts_z2))
        for sport, size, val in sessions:
            n += 1
            aid = f"demo-{n}"
            if sport == "run":
                km, moving = size, int(size * val)
                hr, watts = 146 + random.uniform(-3, 3), 300 + random.uniform(-10, 10)
            else:
                moving, km = size * 60, size * 0.42
                hr, watts = 137 + random.uniform(-3, 3), val
            load = analysis.activity_load(sport, moving, hr, watts, s)
            db.run("""INSERT OR REPLACE INTO activities(id,source,sport,strava_type,start_local,date,name,distance_m,moving_s,
                      elev_m,avg_hr,max_hr,avg_watts,has_hr,load,streams_done,detail_done) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)""",
                   (aid, "demo", sport, "Run" if sport == "run" else "VirtualRide", f"{d.isoformat()}T10:30:00", d.isoformat(),
                    "Demo-Lauf" if sport == "run" else "Demo-Rad", km * 1000, moving, 20, hr, hr + 12, watts, 1, load))
            db.run("INSERT OR REPLACE INTO z2 VALUES(?,?,?,?,?,?,?,?)",
                   (aid, d.isoformat(), sport, moving * 0.6, hr, val if sport == "run" else None,
                    watts if sport == "run" else val, (1000 / val) * 60 / hr if sport == "run" else val / hr))
        # Gesundheitsdaten
        hrv = 58 + 6 * math.sin(weeks_in / 2) + random.uniform(-5, 5)
        db.run("""INSERT OR REPLACE INTO health_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (d.isoformat(), 7 * 3600 + random.uniform(-3000, 2400), random.randint(68, 90), hrv, 58 + 4 * math.sin(weeks_in / 2),
                "BALANCED", 47 - 0.05 * weeks_in + random.uniform(-2, 2), random.randint(80, 100), random.randint(12, 30),
                random.randint(22, 38), random.randint(45, 85), "MODERATE", 53.5, random.randint(6000, 16000), 79.8 + random.uniform(-.4, .4)))
        d += dt.timedelta(days=1)
    for date, label, dist, secs in ((end - dt.timedelta(days=150), "10k", 10000, 2610), (end - dt.timedelta(days=60), "5k", 5000, 1250),
                                    (end - dt.timedelta(days=40), "Halbmarathon", 21097.5, 5920), (end - dt.timedelta(days=24), "Marathon", 42195, 13418)):
        db.run("INSERT OR IGNORE INTO performances(date,label,distance_m,time_s,source,activity_id,note) VALUES(?,?,?,?,?,?,?)",
               (date.isoformat(), label, dist, secs, "demo", "", "synthetisch"))
    print("Plan:", plan_seed.seed_if_empty(), "Einheiten | Demodaten angelegt, Aktivitäten:", n)


if __name__ == "__main__":
    main()
