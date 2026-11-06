"""Kernlogik-Tests (ohne Netzwerk):  python -m unittest discover -s tests -v   (aus dem Projektordner)"""
import datetime as dt
import os
from contextlib import closing
import tempfile
import unittest

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="trainingtest-")
os.environ["MCP_SECRET"] = "s"
os.environ["DASH_PASSWORD"] = "p"

from app import analysis, db, garmin, plan, strava  # noqa: E402
from app.util import fmt_duration, fmt_pace, parse_duration, today  # noqa: E402

db.init()


def reset():
    with closing(db.conn()) as c:
        for t in ("activities", "streams", "z2", "performances", "health_daily", "plan_sessions", "kv", "sync_log"):
            c.execute(f"DELETE FROM {t}")
        c.commit()


class UtilTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(parse_duration("3:20:00"), 12000)
        self.assertEqual(parse_duration("43:00"), 2580)
        self.assertEqual(parse_duration(5400), 5400)
        self.assertEqual(fmt_duration(12000), "3:20:00")
        self.assertEqual(fmt_duration(2580), "43:00")
        self.assertEqual(fmt_pace(299.6), "5:00")
        with self.assertRaises(ValueError):
            parse_duration("abc")


class Z2Tests(unittest.TestCase):
    def test_run_z2_filters_hr_warmup_and_standstill(self):
        t = list(range(0, 1200, 5))                      # 20 min, 5-s-Raster
        hr = [120 if x < 300 else 145 for x in t]        # Einlaufen unter Band, danach Z2
        v = [3.0] * len(t)                               # 3,0 m/s = 5:33 /km
        v[100:110] = [0.2] * 10                          # Stillstand zählt nicht
        r = analysis.z2_from_samples("run", t, hr, v, None, 137, 149)
        self.assertAlmostEqual(r["z2_pace_s_km"], 1000 / 3.0, delta=1)
        self.assertGreater(r["z2_seconds"], 600)
        self.assertAlmostEqual(r["z2_hr"], 145, delta=0.1)

    def test_too_little_z2_returns_none(self):
        t = list(range(0, 900, 5))
        self.assertIsNone(analysis.z2_from_samples("run", t, [160] * len(t), [3.0] * len(t), None, 137, 149))

    def test_ride_power_excludes_coasting(self):
        t = list(range(0, 1800, 5))
        w = [160 if i % 10 else 0 for i in range(len(t))]
        r = analysis.z2_from_samples("ride", t, [140] * len(t), None, w, 137, 149)
        self.assertAlmostEqual(r["z2_watts"], 160, delta=0.5)

    def test_pack_and_compute_roundtrip(self):
        reset()
        t = list(range(0, 1500))
        blob = analysis.pack_streams(t, [145] * len(t), [3.2] * len(t), [250] * len(t))
        db.run("INSERT INTO activities(id,source,sport,date,name) VALUES('a1','strava','run','2026-10-01','x')")
        db.run("INSERT INTO streams VALUES('a1',?)", (blob,))
        self.assertTrue(analysis.compute_z2("a1", "run", "2026-10-01"))
        row = db.one("SELECT * FROM z2 WHERE activity_id='a1'")
        self.assertAlmostEqual(row["z2_pace_s_km"], 1000 / 3.2, delta=1)
        # Z2-Band ändern und aus gespeicherten Streams neu berechnen
        db.update_settings({"hr_z2_low": 150, "hr_z2_high": 160})
        self.assertEqual(analysis.recompute_all_z2(), 0)
        self.assertIsNone(db.one("SELECT * FROM z2 WHERE activity_id='a1'"))


class ForecastTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_riegel_scenarios(self):
        db.update_settings({"race_date": (today() + dt.timedelta(weeks=20)).isoformat(), "goal_time_s": "3:20:00"})
        d = (today() - dt.timedelta(days=30)).isoformat()
        db.run("INSERT INTO performances(date,label,distance_m,time_s,source,activity_id) VALUES(?,?,?,?,?,?)", (d, "10k", 10000, 2608, "t", ""))
        db.run("INSERT INTO performances(date,label,distance_m,time_s,source,activity_id) VALUES(?,?,?,?,?,?)", (d, "Halbmarathon", 21097.5, 5913, "t", ""))
        f = analysis.forecast()
        self.assertTrue(f["available"])
        expect_10k = 2608 * (42195 / 10000) ** 1.06
        expect_hm = 5913 * (42195 / 21097.5) ** 1.06
        by = {s["name"]: s for s in f["scenarios"]}
        self.assertAlmostEqual(by["potential"]["time_s"], min(expect_10k, expect_hm), delta=1)
        self.assertAlmostEqual(by["realistic"]["time_s"], (expect_10k + expect_hm) / 2 * 1.03, delta=1)
        self.assertLess(by["potential"]["time_s"], by["realistic"]["time_s"])
        self.assertLess(by["realistic"]["time_s"], by["conservative"]["time_s"])
        self.assertEqual(f["goal"], "3:20:00")

    def test_old_performances_ignored_and_empty_state(self):
        old = (today() - dt.timedelta(days=500)).isoformat()
        db.run("INSERT INTO performances(date,label,distance_m,time_s,source,activity_id) VALUES(?,?,?,?,?,?)", (old, "10k", 10000, 2400, "t", ""))
        self.assertFalse(analysis.forecast()["available"])

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            db.update_settings({"foo": 1})
        with self.assertRaises(ValueError):
            db.update_settings({"race_date": "kein datum"})
        s = db.update_settings({"scenario_factors": {"realistic": 1.05}})
        self.assertEqual(s["scenario_factors"]["realistic"], 1.05)
        self.assertEqual(s["scenario_factors"]["potential"], 1.0)

    def test_z2_trend_sign(self):
        for i in range(10):                                           # Pace wird über 10 Wochen schneller
            d = (today() - dt.timedelta(weeks=10 - i)).isoformat()
            db.run("INSERT INTO activities(id,source,sport,date,name,duplicate) VALUES(?,?,?,?,?,0)", (f"r{i}", "t", "run", d, "x"))
            db.run("INSERT INTO z2 VALUES(?,?,?,?,?,?,?,?)", (f"r{i}", d, "run", 1800, 145, 380 - 4 * i, None, None))
        z = analysis.z2_series("run", 26)
        self.assertGreater(z["trend"]["pct_per_week"], 0)
        self.assertEqual(len(z["weekly"]), 10)


class PlanTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_crud_and_validation(self):
        s = plan.upsert_session({"date": "2026-11-09", "sport": "run", "title": "Lauf", "distance_km": 10})
        self.assertEqual(s["status"], "planned")
        s = plan.upsert_session({"id": s["id"], "notes": "n"})
        self.assertEqual((s["title"], s["notes"]), ("Lauf", "n"))
        with self.assertRaises(ValueError):
            plan.upsert_session({"date": "2026-11-09", "sport": "yoga", "title": "x"})
        with self.assertRaises(ValueError):
            plan.upsert_session({"sport": "run", "title": "x"})
        self.assertTrue(plan.delete_session(s["id"]))
        self.assertFalse(plan.delete_session(s["id"]))

    def test_replace_range_is_atomic(self):
        plan.upsert_session({"date": "2026-11-10", "sport": "run", "title": "alt"})
        with self.assertRaises(ValueError):
            plan.replace_range("2026-11-09", "2026-11-15", [{"date": "2026-11-11", "sport": "run", "title": "neu"}, {"date": "2026-12-01", "sport": "run", "title": "draußen"}])
        self.assertEqual([s["title"] for s in plan.list_sessions("2026-11-09", "2026-11-15")["sessions"]], ["alt"])
        r = plan.replace_range("2026-11-09", "2026-11-15", [{"date": "2026-11-11", "sport": "ride", "title": "neu"}])
        self.assertEqual((r["removed"], r["added"]), (1, 1))

    def test_effective_status(self):
        ref = dt.date(2026, 11, 10)
        base = {"status": "planned", "sport": "run", "distance_km": 10, "duration_min": 60, "date": "2026-11-09"}
        self.assertEqual(plan.effective_status(base, {"km": 9.0, "min": 55, "n": 1}, ref), "done")
        self.assertEqual(plan.effective_status(base, {"km": 3.0, "min": 20, "n": 1}, ref), "partial")
        self.assertEqual(plan.effective_status(base, None, ref), "missed")
        self.assertEqual(plan.effective_status({**base, "date": "2026-11-10"}, None, ref), "today")
        self.assertEqual(plan.effective_status({**base, "date": "2026-11-12"}, None, ref), "planned")
        self.assertEqual(plan.effective_status({**base, "status": "skipped"}, None, ref), "skipped")
        self.assertEqual(plan.effective_status({**base, "sport": "rest"}, None, ref), "rest")


class FakeStrava:
    """Ersetzt strava._get durch eine Attrappe der Strava-API."""
    def __init__(self, rate_limit_after=None):
        self.calls, self.rate_limit_after = [], rate_limit_after

    def __call__(self, path, params=None):
        self.calls.append(path)
        if self.rate_limit_after is not None and len(self.calls) > self.rate_limit_after:
            raise strava.RateLimited("429")
        if path == "/athlete/activities":
            if params["page"] > 1:
                return []
            d = today().isoformat()
            return [
                {"id": 1, "sport_type": "Run", "name": "Lauf", "start_date_local": f"{d}T10:00:00Z", "distance": 12000, "moving_time": 4000,
                 "average_heartrate": 148, "max_heartrate": 160, "has_heartrate": True, "total_elevation_gain": 10},
                {"id": 2, "sport_type": "VirtualRide", "name": "Rolle", "start_date_local": f"{d}T12:31:00Z", "distance": 40000, "moving_time": 3600,
                 "average_heartrate": 133, "average_watts": 165, "has_heartrate": True},
                {"id": 3, "sport_type": "Ride", "name": "Rolle (Garmin)", "start_date_local": f"{d}T12:30:00Z", "distance": 40500, "moving_time": 3640,
                 "average_heartrate": 133, "average_watts": 164, "has_heartrate": True},
                {"id": 4, "sport_type": "WeightTraining", "name": "Kraft", "start_date_local": f"{d}T18:00:00Z", "distance": 0, "moving_time": 1500},
            ]
        if path.endswith("/streams"):
            n = 1500
            return {"time": {"data": list(range(n))}, "heartrate": {"data": [145] * n},
                    "velocity_smooth": {"data": [3.1] * n}, "watts": {"data": [300] * n}}
        if path == "/activities/1":
            return {"best_efforts": [{"distance": 5000, "elapsed_time": 1500}, {"distance": 10000, "elapsed_time": 3100}, {"distance": 400, "elapsed_time": 90}]}
        return None


class StravaSyncTests(unittest.TestCase):
    def setUp(self):
        reset()
        self._orig = strava._get

    def tearDown(self):
        strava._get = self._orig

    def test_full_sync_with_fake_api(self):
        fake = FakeStrava()
        strava._get = fake
        self.assertEqual(strava.sync_activities(), 4)
        sports = {a["id"]: (a["sport"], a["duplicate"]) for a in db.rows("SELECT id, sport, duplicate FROM activities")}
        self.assertEqual(sports["1"], ("run", 0))
        self.assertEqual(sports["4"], ("strength", 0))
        self.assertEqual(sum(d for _, d in sports.values()), 1, "genau eine der beiden Rollen-Aufzeichnungen ist Dublette")
        res = strava.sync_streams(budget=10)
        self.assertEqual(res["rate_limited"], False)
        self.assertGreaterEqual(res["streams"], 2)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM z2")["n"], 2)
        labels = sorted(r["label"] for r in db.rows("SELECT label FROM performances"))
        self.assertEqual(labels, ["10k", "5k"], "nur Distanzen ab 5 km werden als Bestleistung übernommen")
        # zweiter Lauf lädt nichts erneut
        fake.calls.clear()
        strava.sync_streams(budget=10)
        self.assertEqual(fake.calls, [])

    def test_rate_limit_stops_cleanly_and_resumes(self):
        strava._get = FakeStrava()
        strava.sync_activities()
        strava._get = FakeStrava(rate_limit_after=1)
        res = strava.sync_streams(budget=10)
        self.assertTrue(res["rate_limited"])
        self.assertGreaterEqual(res["remaining"], 1)
        strava._get = FakeStrava()
        res = strava.sync_streams(budget=10)
        self.assertEqual(res["remaining"], 0)

    def test_sport_mapping(self):
        self.assertEqual(strava.sport_of("TrailRun"), "run")
        self.assertEqual(strava.sport_of("GravelRide"), "ride")
        self.assertEqual(strava.sport_of("Swim"), "other")


class FakeGarmin:
    def get_sleep_data(self, d): return {"dailySleepDTO": {"sleepTimeSeconds": 25800, "sleepScores": {"overall": {"value": 81}}}, "avgOvernightHrv": 60}
    def get_hrv_data(self, d): return {"hrvSummary": {"lastNightAvg": 62, "weeklyAvg": 58, "status": "BALANCED"}}
    def get_rhr_day(self, d): return {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": [{"value": 46}]}}}
    def get_body_battery(self, a, b): return [{"bodyBatteryValuesArray": [[1, 30], [2, 95], [3, None]]}]
    def get_stress_data(self, d): return {"avgStressLevel": 28}
    def get_training_readiness(self, d): return [{"score": 64, "level": "MODERATE"}]
    def get_max_metrics(self, d): return [{"generic": {"vo2MaxPreciseValue": 53.4}}]
    def get_stats(self, d): return {"totalSteps": 9000}
    def get_body_composition(self, a, b): raise RuntimeError("API geändert")      # einzelne Metrik darf ausfallen


class GarminTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_parse_and_save_with_partial_failure(self):
        vals, errors = garmin.parse_day(FakeGarmin(), "2026-11-04")
        self.assertEqual(errors, ["weight: RuntimeError"])
        self.assertEqual((vals["sleep_s"], vals["hrv"], vals["rhr"], vals["bb_max"], vals["bb_min"]), (25800, 62, 46, 95, 30))
        self.assertEqual((vals["readiness"], vals["vo2max"], vals["steps"]), (64, 53.4, 9000))
        garmin.save_day("2026-11-04", vals)
        garmin.save_day("2026-11-04", {"weight_kg": 80.0})                 # Teil-Update überschreibt nichts anderes
        row = db.one("SELECT * FROM health_daily WHERE date='2026-11-04'")
        self.assertEqual((row["hrv"], row["weight_kg"]), (62, 80.0))

    def test_dig_is_safe(self):
        self.assertIsNone(garmin.dig({"a": [1]}, "a", 5))
        self.assertIsNone(garmin.dig(None, "a"))
        self.assertEqual(garmin.dig({"a": [{"b": 2}]}, "a", 0, "b"), 2)


class LoadTests(unittest.TestCase):
    def test_load_scales_with_intensity(self):
        s = db.get_settings()
        easy = analysis.activity_load("run", 3600, 140, None, s)
        hard = analysis.activity_load("run", 3600, 165, None, s)
        self.assertAlmostEqual(hard, 100, delta=1)
        self.assertLess(easy, hard)
        self.assertAlmostEqual(analysis.activity_load("run", 3600, None, None, s), 50)


if __name__ == "__main__":
    unittest.main()
