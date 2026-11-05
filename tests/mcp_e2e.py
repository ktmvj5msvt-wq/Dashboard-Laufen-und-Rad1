"""End-to-End-Test des MCP-Endpunkts gegen einen laufenden Server.
    MCP_URL=http://127.0.0.1:8765/mcp/<secret> python tests/mcp_e2e.py
"""
import asyncio
import json
import os

from mcp import Client


def payload(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


async def main():
    url = os.environ["MCP_URL"]
    async with Client(url) as c:
        tools = await c.list_tools()
        names = sorted(t.name for t in tools.tools)
        print("Tools:", names)
        assert len(names) == 14, names

        ov = payload(await c.call_tool("get_overview", {}))
        print("overview keys:", sorted(ov))
        assert ov["forecast"]["available"] and ov["this_week_plan"]

        # Plan bearbeiten: anlegen, ändern, Status setzen, löschen
        new = payload(await c.call_tool("upsert_plan_session", {"date": "2026-12-20", "sport": "run", "title": "MCP-Test", "distance_km": 10, "target": "5:50 /km"}))
        sid = new["id"]
        upd = payload(await c.call_tool("upsert_plan_session", {"id": sid, "distance_km": 12, "notes": "geändert"}))
        assert upd["distance_km"] == 12 and upd["title"] == "MCP-Test", upd
        st = payload(await c.call_tool("set_session_status", {"id": sid, "status": "skipped"}))
        assert st["status"] == "skipped"
        bad = await c.call_tool("upsert_plan_session", {"date": "2026-12-20", "sport": "swimming", "title": "x"})
        assert bad.is_error, "ungültige Sportart muss abgelehnt werden"
        assert payload(await c.call_tool("delete_plan_session", {"id": sid}))["deleted"] is True

        # Zeitraum atomar ersetzen
        r = payload(await c.call_tool("replace_plan_range", {"date_from": "2026-12-21", "date_to": "2026-12-27", "sessions": [
            {"date": "2026-12-21", "sport": "run", "title": "A", "distance_km": 8}, {"date": "2026-12-23", "sport": "ride", "title": "B", "duration_min": 60}]}))
        assert r["added"] == 2, r
        bad = await c.call_tool("replace_plan_range", {"date_from": "2026-12-21", "date_to": "2026-12-27", "sessions": [{"date": "2027-01-05", "sport": "run", "title": "außerhalb"}]})
        assert bad.is_error
        plan = payload(await c.call_tool("get_plan", {"date_from": "2026-12-21", "date_to": "2026-12-27"}))
        assert [s["title"] for s in plan["sessions"]] == ["A", "B"], "fehlgeschlagenes Ersetzen darf nichts ändern"
        payload(await c.call_tool("replace_plan_range", {"date_from": "2026-12-21", "date_to": "2026-12-27", "sessions": []}))

        # Einstellungen und Prognose
        s = payload(await c.call_tool("update_settings", {"changes": {"goal_time_s": "3:15:00"}}))
        assert s["goal_time_s"] == 11700
        f = payload(await c.call_tool("get_forecast", {}))
        assert f["goal"] == "3:15:00" and len(f["scenarios"]) == 3
        bad = await c.call_tool("update_settings", {"changes": {"unbekannt": 1}})
        assert bad.is_error
        payload(await c.call_tool("update_settings", {"changes": {"goal_time_s": "3:20:00"}}))
        z = payload(await c.call_tool("get_z2_history", {"sport": "run", "weeks": 12}))
        assert z["trend"] is not None
        ap = payload(await c.call_tool("add_performance", {"date": "2026-10-11", "distance_m": 10000, "time": "43:00", "note": "Test"}))
        assert ap["label"] == "10k" and ap["pace"] == "4:18", ap
        h = payload(await c.call_tool("get_health", {"days": 14}))
        assert h["form"]
        acts = await c.call_tool("get_recent_activities", {"days": 7})
        print("aktivitäten-Ergebnis ok:", not acts.is_error)
        print("ALLE MCP-TESTS BESTANDEN")


asyncio.run(main())
