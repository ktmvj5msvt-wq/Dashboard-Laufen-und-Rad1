"""Orchestrierung der Datenabrufe (manuell, per Hintergrund-Schleife oder über den MCP-Konnektor)."""
import threading

from . import analysis, db, garmin, strava

_lock = threading.Lock()


def run_sync(source: str = "all") -> dict:
    """source: 'all' | 'strava' | 'garmin' | 'z2' (Z2 aus gespeicherten Streams neu berechnen)."""
    if not _lock.acquire(blocking=False):
        return {"status": "busy", "message": "Ein Sync läuft bereits"}
    result: dict = {}
    try:
        if source in ("all", "strava"):
            if not strava.connected():
                result["strava"] = {"ok": False, "message": "nicht verbunden (/auth/strava öffnen)"}
            else:
                try:
                    n = strava.sync_activities()
                    st = strava.sync_streams()
                    result["strava"] = {"ok": True, "activities": n, **st}
                    msg = f"{n} Aktivitäten, {st['streams']} Streams, {st['details']} Details"
                    if st["rate_limited"]:
                        msg += " (Rate-Limit, Rest folgt beim nächsten Sync)"
                    db.log_sync("strava", True, msg)
                except Exception as e:  # noqa: BLE001
                    result["strava"] = {"ok": False, "message": f"{type(e).__name__}: {e}"}
                    db.log_sync("strava", False, f"{type(e).__name__}: {e}")
        if source in ("all", "garmin"):
            if not garmin.configured():
                result["garmin"] = {"ok": False, "message": "nicht eingerichtet (GARMIN_EMAIL/GARMIN_PASSWORD)"}
            else:
                try:
                    r = garmin.sync_health()
                    result["garmin"] = {"ok": True, **r}
                    db.log_sync("garmin", True, f"{r['days']} Tage, {r['errors']} Teilfehler")
                except Exception as e:  # noqa: BLE001
                    result["garmin"] = {"ok": False, "message": f"{type(e).__name__}: {e}"}
                    db.log_sync("garmin", False, f"{type(e).__name__}: {e}")
        if source == "z2":
            result["z2"] = {"ok": True, "recomputed": analysis.recompute_all_z2()}
        result["status"] = "ok"
    finally:
        _lock.release()
    return result


def status() -> dict:
    out = {"strava": {"configured": strava.configured(), "connected": strava.connected()},
           "garmin": {"configured": garmin.configured()}}
    for src in ("strava", "garmin"):
        last = db.one("SELECT ts, ok, message FROM sync_log WHERE source=? ORDER BY id DESC LIMIT 1", (src,))
        ok = db.one("SELECT ts FROM sync_log WHERE source=? AND ok=1 ORDER BY id DESC LIMIT 1", (src,))
        out[src].update(last=last, last_ok=ok["ts"] if ok else None)
    out["counts"] = {
        "activities": db.one("SELECT COUNT(*) AS n FROM activities WHERE duplicate=0")["n"],
        "duplicates": db.one("SELECT COUNT(*) AS n FROM activities WHERE duplicate=1")["n"],
        "z2_runs": db.one("SELECT COUNT(*) AS n FROM z2 WHERE sport='run'")["n"],
        "z2_rides": db.one("SELECT COUNT(*) AS n FROM z2 WHERE sport='ride'")["n"],
        "streams_pending": db.one("SELECT COUNT(*) AS n FROM activities WHERE source='strava' AND duplicate=0 "
                                  "AND sport IN ('run','ride') AND streams_done=0 AND has_hr=1")["n"],
        "health_days": db.one("SELECT COUNT(*) AS n FROM health_daily")["n"],
    }
    return out
