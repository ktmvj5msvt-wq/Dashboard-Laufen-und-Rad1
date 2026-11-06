"""End-to-End-Test: startet den echten Server (mit den Parametern des systemd-Dienstes) und prüft
Passwortschutz, Sperre über X-Forwarded-For, CSRF-Schutz und alle MCP-Tools. Eigene Testdaten, kein Netzwerkzugriff nötig.

    python tests/server_e2e.py
"""
import asyncio
import base64
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = tempfile.mkdtemp(prefix="training-e2e-")
SECRET, USER, PW = "e2e-geheimnis", "trainer", "e2e-passwort"
os.environ.update(DATA_DIR=DATA, MCP_SECRET=SECRET, DASH_USER=USER, DASH_PASSWORD=PW, SYNC_INTERVAL_MIN="0", SEED_PLAN="0")
sys.path.insert(0, str(ROOT))

from mcp import Client  # noqa: E402

from app import db, plan  # noqa: E402


def fixture():
    """Testdaten, die in jedem Betrieb vorkommen können (Läufe mit Z2, Bestleistungen, Gesundheit, Plan)."""
    db.init()
    today = dt.date.today()
    for i in range(10):
        d = (today - dt.timedelta(weeks=10 - i)).isoformat()
        db.run("INSERT INTO activities(id,source,sport,date,start_local,name,distance_m,moving_s,avg_hr,has_hr,load) VALUES(?,?,?,?,?,?,?,?,?,1,60)",
               (f"r{i}", "test", "run", d, d + "T10:00:00", "Lauf", 10000, 3600, 146))
        db.run("INSERT INTO z2 VALUES(?,?,?,?,?,?,?,?)", (f"r{i}", d, "run", 1800, 145, 380 - 4 * i, 300, 1.4))
    d = (today - dt.timedelta(days=30)).isoformat()
    db.run("INSERT INTO performances(date,label,distance_m,time_s,source,activity_id) VALUES(?,?,?,?,?,?)", (d, "10k", 10000, 2608, "test", ""))
    db.run("INSERT INTO performances(date,label,distance_m,time_s,source,activity_id) VALUES(?,?,?,?,?,?)", (d, "Halbmarathon", 21097.5, 5913, "test", ""))
    db.run("INSERT INTO health_daily(date,sleep_s,hrv,rhr,bb_max) VALUES(?,?,?,?,?)", (today.isoformat(), 25000, 61, 46, 90))
    plan.upsert_session({"date": today.isoformat(), "sport": "run", "title": "Heutiger Lauf", "distance_km": 8})


def http(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def basic(pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{USER}:{pw}".encode()).decode()}


def payload(result):
    return result.structured_content or json.loads(result.content[0].text)


async def mcp_suite(url):
    async with Client(url) as c:
        names = sorted(t.name for t in (await c.list_tools()).tools)
        assert len(names) == 14, names
        ov = payload(await c.call_tool("get_overview", {}))
        assert ov["forecast"]["available"] and ov["this_week_plan"], ov["this_week_plan"]

        new = payload(await c.call_tool("upsert_plan_session", {"date": "2026-12-20", "sport": "run", "title": "MCP-Test", "distance_km": 10, "target": "5:50 /km"}))
        upd = payload(await c.call_tool("upsert_plan_session", {"id": new["id"], "distance_km": 12, "notes": "geändert"}))
        assert upd["distance_km"] == 12 and upd["title"] == "MCP-Test", upd
        assert payload(await c.call_tool("set_session_status", {"id": new["id"], "status": "skipped"}))["status"] == "skipped"
        assert (await c.call_tool("upsert_plan_session", {"date": "2026-12-20", "sport": "swimming", "title": "x"})).is_error
        assert payload(await c.call_tool("delete_plan_session", {"id": new["id"]}))["deleted"] is True

        r = payload(await c.call_tool("replace_plan_range", {"date_from": "2026-12-21", "date_to": "2026-12-27", "sessions": [
            {"date": "2026-12-21", "sport": "run", "title": "A", "distance_km": 8}, {"date": "2026-12-23", "sport": "ride", "title": "B", "duration_min": 60}]}))
        assert r["added"] == 2, r
        assert (await c.call_tool("replace_plan_range", {"date_from": "2026-12-21", "date_to": "2026-12-27",
                "sessions": [{"date": "2027-01-05", "sport": "run", "title": "außerhalb"}]})).is_error
        titles = [s["title"] for s in payload(await c.call_tool("get_plan", {"date_from": "2026-12-21", "date_to": "2026-12-27"}))["sessions"]]
        assert titles == ["A", "B"], f"fehlgeschlagenes Ersetzen darf nichts ändern: {titles}"
        payload(await c.call_tool("replace_plan_range", {"date_from": "2026-12-21", "date_to": "2026-12-27", "sessions": []}))

        assert payload(await c.call_tool("update_settings", {"changes": {"goal_time_s": "3:15:00"}}))["goal_time_s"] == 11700
        f = payload(await c.call_tool("get_forecast", {}))
        assert f["goal"] == "3:15:00" and len(f["scenarios"]) == 3
        assert (await c.call_tool("update_settings", {"changes": {"unbekannt": 1}})).is_error
        assert payload(await c.call_tool("get_z2_history", {"sport": "run", "weeks": 12}))["trend"] is not None
        ap = payload(await c.call_tool("add_performance", {"date": "2026-10-11", "distance_m": 10000, "time": "43:00", "note": "Test"}))
        assert ap["label"] == "10k" and ap["pace"] == "4:18", ap
        assert payload(await c.call_tool("get_health", {"days": 14}))["form"]
        assert not (await c.call_tool("get_recent_activities", {"days": 90})).is_error
        assert not (await c.call_tool("trigger_sync", {"source": "z2"})).is_error


def main():
    fixture()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--proxy-headers",
                               "--forwarded-allow-ips", "127.0.0.1", "--no-server-header", "--log-level", "warning"],
                              cwd=ROOT, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                if http(base + "/healthz")[0] == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        else:
            raise SystemExit("Server startet nicht")

        assert http(base + "/")[0] == 401, "ohne Passwort muss 401 kommen"
        status, headers, body = http(base + "/", headers=basic(PW))
        assert status == 200 and b"Trainingsdashboard" in body
        assert "server" not in {k.lower() for k in headers}, "Server-Header soll nicht verraten werden"
        assert http(base + "/api/plan", headers=basic("falsch"))[0] == 401

        ip_a, ip_b = {"X-Forwarded-For": "203.0.113.7"}, {"X-Forwarded-For": "203.0.113.8"}
        for _ in range(8):
            assert http(base + "/", headers={**basic("falsch"), **ip_a})[0] == 401
        assert http(base + "/", headers={**basic(PW), **ip_a})[0] == 429, "Sperre muss pro Client-IP aus X-Forwarded-For greifen"
        assert http(base + "/", headers={**basic(PW), **ip_b})[0] == 200, "andere Client-IP bleibt nutzbar"

        assert http(base + "/api/sync?source=z2", "POST", {**basic(PW), "Origin": "https://evil.example"})[0] == 403
        assert http(base + "/api/sync?source=z2", "POST", {**basic(PW), "Origin": base})[0] == 200

        assert http(f"{base}/mcp/falsch", "POST", {"content-type": "application/json"}, b"{}")[0] == 404
        asyncio.run(mcp_suite(f"{base}/mcp/{SECRET}"))
        print("ALLE E2E-TESTS BESTANDEN")
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
