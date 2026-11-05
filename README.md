# Trainingsdashboard (Laufen & Rad)

Ein kleiner Server, der Strava- und Garmin-Daten einliest, daraus Z2-Verlauf, Belastung und eine Marathon-Prognose berechnet und einen Trainingsplan führt. Claude bearbeitet Plan, Einstellungen und Referenzleistungen über einen Konnektor (MCP).

**Reiter**
1. **Gesundheit**: Schlaf, HRV, Ruhepuls, Body Battery, Readiness, Stress, VO₂max, Gewicht, dazu Fitness/Ermüdung/Form.
2. **Trainingsplan**: Wochenansicht mit automatischem Abgleich gegen Strava (erledigt, teilweise, verpasst), Soll/Ist-Umfang.
3. **Prognose & Z2-Verlauf**: Marathon-Szenarien mit Abstand zum Ziel, Z2-Pace (Lauf) und Z2-Watt (Rad) über die Zeit mit Trend und Fortschreibung bis zum Renntag, Wochenumfänge.

## Schnellstart (Docker)

```bash
cp .env.example .env          # BASE_URL, Strava-Zugang, Garmin-Zugang eintragen
docker compose up -d --build
```

Das Dashboard läuft auf Port 8000 (nur lokal gebunden). Für den Zugriff von außen, und damit Claude den Konnektor erreicht, braucht der Server eine **HTTPS-Adresse**: entweder ein vorhandener Reverse-Proxy oder das mitgelieferte Caddy (Domain in `Caddyfile` eintragen, dann `docker compose --profile https up -d`).

Ohne Docker: `pip install -r requirements.txt`, dann `uvicorn app.main:app --host 0.0.0.0 --port 8000` (Umgebungsvariablen wie in `.env.example`).

Beim ersten Start entstehen in `data/` die Datenbank sowie `dash_password` und `mcp_secret`, falls du sie nicht selbst setzt. Anmeldung im Browser: Benutzer `trainer` (oder `DASH_USER`) und das Passwort.

## Strava verbinden

1. Unter <https://www.strava.com/settings/api> eine Anwendung anlegen. *Authorization Callback Domain* = Domain aus `BASE_URL`.
2. `STRAVA_CLIENT_ID` und `STRAVA_CLIENT_SECRET` in `.env` setzen, Server neu starten.
3. Im Dashboard auf den Hinweis „Jetzt mit Strava verbinden“ klicken und zustimmen.

Der erste Sync lädt die Aktivitäten der letzten `HISTORY_DAYS` (450) Tage. Für die Z2-Auswertung braucht es je Aktivität einen Stream-Abruf. Strava erlaubt nur 100 Anfragen je 15 Minuten, deshalb werden pro Sync höchstens `STREAM_BUDGET` (20) Aktivitäten nachgeladen, die neuesten zuerst, und der Rest folgt automatisch. Bei etwa 150 Aktivitäten ist der Rückstand nach wenigen Stunden aufgeholt. Die Streams werden ausgedünnt gespeichert, sodass eine Änderung des Z2-Bands ohne neue Abrufe möglich ist (`trigger_sync("z2")`).

Doppelte Aufzeichnungen derselben Einheit (z. B. Rolle in MyWhoosh und zusätzlich Garmin, gleicher Start innerhalb von 10 Minuten, ähnliche Dauer) zählt das Dashboard nur einmal.

## Garmin verbinden

Garmin bietet für Privatpersonen keine offizielle API. Genutzt wird die inoffizielle Bibliothek `garminconnect`. Sie kann sich ohne Vorankündigung ändern, und Garmin kann Anmeldungen drosseln. Jede Metrik wird einzeln abgefragt, ein Ausfall bricht den Sync nicht ab.

- Ohne Zwei-Faktor: `GARMIN_EMAIL` und `GARMIN_PASSWORD` in `.env`.
- Mit Zwei-Faktor: einmalig `docker compose run --rm dashboard python -m app.garmin_login` (fragt Zugangsdaten und Code ab und legt Token in `data/garmin_tokens` ab).

## Claude-Konnektor

Im Dashboard unten unter „Claude-Konnektor einrichten“ steht die URL (`https://deine-domain/mcp/<Geheimnis>`). In Claude: *Einstellungen → Konnektoren → Benutzerdefinierten Konnektor hinzufügen* und die URL eintragen.

**Sicherheit:** Die URL ist das Zugangsgeheimnis (Claude-Konnektoren unterstützen hier keinen Basic-Auth-Header). Behandle sie wie ein Passwort, nutze HTTPS und erzeuge sie bei Verdacht neu (`MCP_SECRET` ändern oder `data/mcp_secret` löschen, Server neu starten). Wer die URL kennt, kann Plan und Einstellungen lesen und ändern.

**Tools**

| Tool | Zweck |
|---|---|
| `get_overview` | Alles auf einen Blick, guter Einstieg |
| `get_plan`, `upsert_plan_session`, `delete_plan_session`, `set_session_status`, `replace_plan_range` | Plan lesen und bearbeiten; `replace_plan_range` ersetzt einen Zeitraum atomar (alles oder nichts) |
| `get_forecast`, `get_z2_history`, `get_health`, `get_recent_activities` | Auswertungen |
| `get_settings`, `update_settings`, `add_performance` | Renndatum, Zielzeit, Z2-Band, LTHR, FTP, Szenariofaktoren, Wettkampfzeiten |
| `trigger_sync` | Datenabruf anstoßen (`all`, `strava`, `garmin`, `z2`) |

Beispiele: „Plane die nächsten zwei Wochen neu, meine HRV ist gesunken“, „Setze die Zielzeit auf 3:15:00“, „Trage meinen 10er vom Sonntag mit 42:30 ein“.

## Wie gerechnet wird

- **Z2-Pace/-Watt:** Mittelwert über alle Messpunkte einer Einheit, deren Herzfrequenz im Z2-Band liegt (Standard 137–149 bpm), ohne die ersten 5 Minuten und ohne Stillstand/Rollen. Mindestens 10 Minuten in Z2 nötig. Wochenmittel sind nach Z2-Zeit gewichtet; die Pace wird über die Geschwindigkeit gemittelt. Der Trend ist eine Gerade über die letzten (bis zu) 12 Wochenwerte, mindestens 4. Hitze, Müdigkeit und Gelände verfälschen einzelne Werte, aussagekräftig ist der Verlauf.
- **Belastung:** Dauer × (Herzfrequenzreserve-Anteil)² × 100 je Stunde, sodass 1 h bei der Schwellenfrequenz etwa 100 ergibt. Fitness = 42-Tage-, Ermüdung = 7-Tage-Mittel, Form = Fitness − Ermüdung. Es ist eine Schätzung, nicht der Wert von Garmin oder TrainingPeaks.
- **Marathon-Prognose:** Riegel-Formel (Exponent 1,06) aus der besten 5-km-, 10-km- und Halbmarathon-Zeit im Referenzfenster (240 Tage). *Potenzial* = schnellster Bezugswert, *Realistisch* = Median × 1,03, *Konservativ* = Median × 1,07. Der Z2-Trend verschiebt die Prognose zusätzlich, auf 50 % gedämpft und höchstens ±5 %. Strava-Bestwerte aus Trainingsläufen unterschätzen das Wettkampfniveau meist; echte Wettkampfzeiten lassen sich mit `add_performance` eintragen. Die Faktoren sind Annahmen und über `update_settings` anpassbar. Die Prognose ist eine Orientierung, keine Garantie.

## Startplan

Beim ersten Start legt das Dashboard den Plan 02.11.–13.12.2026 an (Wiederaufbau nach München, Hüfte schonen, danach Umfang und Schwelle), sofern die Plantabelle leer ist (`SEED_PLAN=0` zum Abschalten). Danach pflegt Claude den Plan über den Konnektor. Zielzeit (Standard 3:20:00) und Renndatum (04.04.2027) sind Platzhalter, bitte anpassen.

## Entwicklung und Tests

```bash
python -m unittest discover -s tests -v          # Kernlogik, Strava mit simulierter API, Garmin mit Attrappe
DATA_DIR=./demo-data python -m scripts.seed_demo  # synthetische Demodaten (nur zum Ausprobieren der Oberfläche)
DATA_DIR=./demo-data MCP_SECRET=x DASH_PASSWORD=x uvicorn app.main:app
MCP_URL=http://127.0.0.1:8000/mcp/x python tests/mcp_e2e.py   # End-to-End-Test der MCP-Tools
```

Das Dashboard hat keine externen Abhängigkeiten im Browser (Diagramme sind eigener SVG-Code) und funktioniert deshalb auch ohne Internetzugriff des Clients.

## Bekannte Grenzen

- Die Strava- und Garmin-Anbindung wurde nur gegen Attrappen getestet, nicht gegen die echten Dienste (in der Entwicklungsumgebung gab es keinen Zugang). Feldnamen der Garmin-Antworten sind aus der Bibliothek abgeleitet und können abweichen. Im Dashboard erscheinen fehlgeschlagene Syncs als Hinweis, im Log steht der Grund.
- Ein einzelner Nutzer, eine SQLite-Datei. Sicherung: den Ordner `data/` kopieren.
