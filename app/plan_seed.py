"""Startplan 02.11.–13.12.2026 (Wiederaufbau nach München, Hüfte schonen, dann Umfang und Schwelle).
Wird nur angelegt, wenn die Plan-Tabelle leer ist. Danach pflegt Claude den Plan über den Konnektor."""
import datetime as dt

from . import db, plan

EASY = "5:50–6:15 /km, HF < 150"
Z1 = "150–170 W (Z1/Z2 niedrig)"
Z2 = "160–185 W (Z2)"
KRAFT = "Hüfte, Gesäß (Seitstütz, Hip Thrust, einbeinig), Rumpf, Waden"


def _run(day, km, title="Lockerer Lauf", desc="", target=EASY, kind="easy"):
    return (day, "run", kind, title, desc, round(km * 6.1), km, target)


def _ride(day, minutes, title="Rad locker", target=Z1, kind="recovery", desc=""):
    return (day, "ride", kind, title, desc, minutes, None, target)


def _kraft(day):
    return (day, "strength", "strength", "Kraft & Stabilität", KRAFT, 25, None, "kontrolliert, kein Muskelversagen")


WEEKS = {
    "2026-11-02": [
        _run(0, 12), _ride(0, 60), _ride(1, 60), _kraft(1), _run(2, 8),
        _ride(3, 75, "Rad Z2", Z2, "endurance"),
        _run(4, 6, desc="Am Ende 4–5 Steigerungen à 15–20 s"),
        _ride(5, 60, "Rad locker (optional)"), _kraft(5), _run(6, 12, "Lockerer Lauf (Wochenende)"),
    ],
    "2026-11-09": [
        _run(0, 10), _ride(0, 60), _ride(1, 75), _kraft(1),
        _run(2, 11, desc="2×6 Steigerungen à 20 s am Ende"),
        _ride(3, 90, "Rad Z2", Z2, "endurance"), _run(4, 8),
        _ride(5, 60), _kraft(5), _run(6, 16, "Langer lockerer Lauf", kind="long"),
    ],
    "2026-11-16": [
        _run(0, 10), _ride(1, 75), _kraft(1),
        _run(2, 12, "Fahrtspiel", "Nach 3 km: 8×1 Min flott (4:20–4:30 /km), 1 Min Trab", "flott ≈ 4:20–4:30 /km", "quality"),
        _ride(3, 90, "Rad Z2", Z2, "endurance"), _run(4, 10),
        _ride(5, 60), _kraft(5), _run(6, 18, "Langer lockerer Lauf", kind="long"),
    ],
    "2026-11-23": [
        _run(0, 8), _ride(1, 60), _kraft(1),
        _run(2, 10, "Fahrtspiel (Entlastungswoche)", "6×1 Min flott, 1 Min Trab", "flott ≈ 4:25–4:35 /km", "quality"),
        _ride(3, 75, "Rad Z2", Z2, "endurance"), _run(4, 8), _ride(5, 60),
        _run(6, 16, "Langer lockerer Lauf", kind="long"),
    ],
    "2026-11-30": [
        _run(0, 10), _ride(1, 75), _kraft(1),
        _run(2, 14, "Schwelle 3×8 Min", "2 km Einlaufen, 3×8 Min Schwelle mit 2 Min Trab, Auslaufen", "≈ 4:40 /km", "quality"),
        _ride(3, 90, "Rad Z2", Z2, "endurance"), _run(4, 10),
        _run(5, 6), _kraft(5), _run(6, 18, "Langer lockerer Lauf", kind="long"),
    ],
    "2026-12-07": [
        _run(0, 10), _run(1, 8), _ride(1, 60),
        _run(2, 15, "Schwelle 2×12 Min", "2 km Einlaufen, 2×12 Min Schwelle mit 3 Min Trab, Auslaufen", "≈ 4:40 /km", "quality"),
        _ride(3, 90, "Rad Z2", Z2, "endurance"), _run(4, 10),
        _ride(5, 60), _kraft(5), _run(6, 20, "Langer lockerer Lauf", kind="long"),
    ],
}


def seed_if_empty() -> int:
    if db.one("SELECT id FROM plan_sessions LIMIT 1"):
        return 0
    n = 0
    for monday, items in WEEKS.items():
        start = dt.date.fromisoformat(monday)
        for day, sport, kind, title, desc, minutes, km, target in items:
            plan.upsert_session({"date": (start + dt.timedelta(days=day)).isoformat(), "sport": sport, "kind": kind,
                                 "title": title, "description": desc, "duration_min": minutes, "distance_km": km,
                                 "target": target})
            n += 1
    return n
