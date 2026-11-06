"""HTTP-Schicht ohne Netzwerk: Passwortschutz, Sperre, Header, CSRF, MCP-Pfad.   python -m unittest discover -s tests -v"""
import asyncio
import base64
import os
import tempfile
import unittest

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="trainingtest-http-")
os.environ["MCP_SECRET"] = "geheimer-pfad-123"
os.environ["DASH_USER"] = "trainer"
os.environ["DASH_PASSWORD"] = "pässwort-mit-umlaut"
os.environ["SYNC_INTERVAL_MIN"] = "0"

from app import config, db, main, strava  # noqa: E402

# Falls ein anderer Test das Konfigurationsmodul schon geladen hat, Testwerte direkt setzen.
config.MCP_SECRET, config.DASH_USER, config.DASH_PASSWORD = "geheimer-pfad-123", "trainer", "pässwort-mit-umlaut"
db.init()


def basic(user, pw):
    return {"authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


async def call(path, method="GET", headers=None, ip="10.0.0.1", query=b""):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "https",
             "path": path, "raw_path": path.encode(), "query_string": query, "root_path": "", "server": ("t", 443),
             "client": (ip, 1234), "headers": [(k.lower().encode(), v.encode("latin-1")) for k, v in (headers or {}).items()]}
    msgs = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(m):
        msgs.append(m)

    await main.app(scope, receive, send)
    start = next(m for m in msgs if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in msgs if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, body


def run(coro):
    return asyncio.run(coro)


GOOD = basic("trainer", "pässwort-mit-umlaut")


class AuthTests(unittest.TestCase):
    def setUp(self):
        main._fails.clear()

    def test_page_requires_password(self):
        status, headers, _ = run(call("/"))
        self.assertEqual(status, 401)
        self.assertIn("Basic", headers["www-authenticate"])
        for path in ("/api/status", "/api/plan", "/api/health", "/static/app.js", "/auth/strava"):
            self.assertEqual(run(call(path))[0], 401, path)

    def test_correct_password_with_umlaut_and_headers(self):
        status, headers, body = run(call("/", headers=GOOD))
        self.assertEqual(status, 200)
        self.assertIn(b"Trainingsdashboard", body)
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertIn("script-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["cache-control"], "no-store")

    def test_wrong_credentials_including_non_ascii_do_not_crash(self):
        for hdr in (basic("trainer", "falsch"), basic("hacker", "pässwort-mit-umlaut"), basic("trainer", "ÿ€€"),
                    {"authorization": "Basic !!!nicht-base64!!!"}, {"authorization": "Bearer x"}):
            self.assertEqual(run(call("/", headers=hdr, ip="10.0.0.9"))[0], 401, hdr)

    def test_lockout_after_repeated_failures_per_ip(self):
        for _ in range(main.MAX_FAILS):
            self.assertEqual(run(call("/", headers=basic("trainer", "falsch"), ip="10.0.0.2"))[0], 401)
        status, headers, _ = run(call("/", headers=GOOD, ip="10.0.0.2"))
        self.assertEqual(status, 429, "auch das richtige Passwort wird während der Sperre abgelehnt")
        self.assertGreater(int(headers["retry-after"]), 0)
        self.assertEqual(run(call("/", headers=GOOD, ip="10.0.0.3"))[0], 200, "andere IPs bleiben unberührt")

    def test_first_visit_without_header_is_not_a_failure(self):
        for _ in range(main.MAX_FAILS + 2):
            self.assertEqual(run(call("/", ip="10.0.0.4"))[0], 401)
        self.assertEqual(run(call("/", headers=GOOD, ip="10.0.0.4"))[0], 200)

    def test_successful_login_resets_counter(self):
        for _ in range(main.MAX_FAILS - 1):
            run(call("/", headers=basic("trainer", "falsch"), ip="10.0.0.5"))
        self.assertEqual(run(call("/", headers=GOOD, ip="10.0.0.5"))[0], 200)
        self.assertEqual(run(call("/", headers=basic("trainer", "falsch"), ip="10.0.0.5"))[0], 401)

    def test_healthz_open_and_minimal(self):
        status, _, body = run(call("/healthz"))
        self.assertEqual((status, body), (200, b"ok"))


class CsrfTests(unittest.TestCase):
    def setUp(self):
        main._fails.clear()

    def test_cross_origin_post_blocked(self):
        h = {**GOOD, "host": "training.example.com", "origin": "https://evil.example"}
        self.assertEqual(run(call("/api/sync", "POST", h, query=b"source=z2"))[0], 403)
        self.assertEqual(run(call("/api/plan/1/status", "POST", h))[0], 403)

    def test_same_origin_and_non_browser_post_allowed(self):
        same = {**GOOD, "host": "training.example.com", "origin": "https://training.example.com"}
        self.assertEqual(run(call("/api/sync", "POST", same, query=b"source=z2"))[0], 200)
        self.assertEqual(run(call("/api/sync", "POST", GOOD, query=b"source=z2"))[0], 200)


class McpPathTests(unittest.TestCase):
    def test_only_exact_secret_reaches_mcp(self):
        for path in ("/mcp", "/mcp/", "/mcp/falsch", "/mcp/geheimer-pfad-12", "/mcp/geheimer-pfad-123/x"):
            self.assertEqual(run(call(path, "POST"))[0], 404, path)
        self.assertTrue(main._is_mcp_path("/mcp/geheimer-pfad-123"))
        self.assertTrue(main._is_mcp_path("/mcp/geheimer-pfad-123/"))
        self.assertFalse(main._is_mcp_path("/mcp/geheimer-pfad-123x"))


class StravaOAuthTests(unittest.TestCase):
    def test_state_is_required_and_single_use(self):
        main._fails.clear()
        strava_cfg = (main.config.STRAVA_CLIENT_ID, main.config.STRAVA_CLIENT_SECRET)
        main.config.STRAVA_CLIENT_ID = strava.config.STRAVA_CLIENT_ID = "123"
        main.config.STRAVA_CLIENT_SECRET = strava.config.STRAVA_CLIENT_SECRET = "abc"
        try:
            status, headers, _ = run(call("/auth/strava", headers=GOOD))
            self.assertEqual(status, 307)
            state = headers["location"].split("state=")[1].split("&")[0]
            # falscher state, dann (nach Verbrauch) auch der richtige
            self.assertEqual(run(call("/auth/strava/callback", headers=GOOD, query=b"code=x&state=falsch"))[0], 400)
            self.assertEqual(run(call("/auth/strava/callback", headers=GOOD, query=f"code=x&state={state}".encode()))[0], 400)
            self.assertEqual(run(call("/auth/strava/callback", headers=GOOD, query=b"code=x"))[0], 400)
        finally:
            main.config.STRAVA_CLIENT_ID = strava.config.STRAVA_CLIENT_ID = strava_cfg[0]
            main.config.STRAVA_CLIENT_SECRET = strava.config.STRAVA_CLIENT_SECRET = strava_cfg[1]


if __name__ == "__main__":
    unittest.main()
