# Training Dashboard (Laufen & Radfahren)

Dashboard mit drei Reitern – **Gesundheit**, **Trainingsplan**, **Prognose & Z2-Verlauf** –, das Strava- und (optional) Garmin-Daten
liest, daraus Prognosen ableitet und sich über einen **Claude-Konnektor** wiederholt bearbeiten lässt.
Läuft ausschließlich auf einem **Ubuntu-Server (22.04 / 24.04)** hinter einer eigenen **Domain mit HTTPS** und **Passwortschutz**.

## Voraussetzungen

- VPS mit Ubuntu 22.04 oder 24.04, root-/sudo-Zugang, mindestens 1 GB RAM
- eine Domain/Subdomain, deren **A-Record** (ggf. AAAA) auf die Server-IP zeigt
- Ports **80 und 443** von außen erreichbar (80 braucht Caddy für das Zertifikat)

## Installation

```bash
unzip training-dashboard.zip && cd training-dashboard
sudo ./deploy/install.sh --domain training.example.com --email du@example.com
```

Das Skript ist wiederholbar (erneut ausführen = Update, Passwörter und Daten bleiben). Mit `--dry-run` zeigt es nur, was es täte.
Es installiert Python-venv, Caddy (automatisches Let's-Encrypt-Zertifikat), den systemd-Dienst `training-dashboard`, die Firewall
(ufw: SSH, 80, 443; abschaltbar mit `--no-firewall`) und ein tägliches Datenbank-Backup. Am Ende werden Benutzername, Passwort und
Konnektor-URL angezeigt; jederzeit erneut mit `sudo training-dashboard-info`.

## Passwortschutz

- Jeder Aufruf der Seite und der API verlangt HTTP-Basic-Auth (Benutzer + Passwort aus `/etc/training-dashboard/env`). Das Passwort wird bei der Installation zufällig erzeugt.
- Nach 8 falschen Anmeldungen innerhalb von 10 Minuten wird die jeweilige Client-IP 10 Minuten gesperrt (HTTP 429).
- HTTPS erzwungen durch Caddy (HSTS), Sicherheits-Header (CSP, X-Frame-Options …), Schutz vor Cross-Site-Anfragen bei schreibenden Aufrufen.
- Der Claude-Konnektor nutzt statt Passwort eine **geheime URL** `https://DOMAIN/mcp/<MCP_SECRET>` (nur `/healthz` ist offen).
- Der Dienst läuft als eigener Benutzer ohne Login mit systemd-Härtung und lauscht nur auf `127.0.0.1`.

Passwort ändern: `DASH_PASSWORD` in `/etc/training-dashboard/env` setzen, dann `sudo systemctl restart training-dashboard`.
Konnektor-URL erneuern: `MCP_SECRET` ändern (z. B. `openssl rand -hex 24`), neu starten, URL im Konnektor austauschen.

## Strava verbinden

1. Auf https://www.strava.com/settings/api eine App anlegen, *Authorization Callback Domain* = deine Domain.
2. `STRAVA_CLIENT_ID` und `STRAVA_CLIENT_SECRET` in `/etc/training-dashboard/env` eintragen, Dienst neu starten.
3. `https://DOMAIN/auth/strava` öffnen und freigeben. Der erste Abruf lädt die letzten ~450 Tage; Streams für die Z2-Auswertung kommen
   in Portionen (Rate-Limit 100 Anfragen/15 min), der Rest folgt automatisch im stündlichen Sync.

## Garmin verbinden (optional)

`GARMIN_EMAIL` / `GARMIN_PASSWORD` in die env-Datei, dann `sudo training-dashboard-garmin-login` (fragt ggf. den MFA-Code) und
`sudo systemctl restart training-dashboard`. Es wird die inoffizielle Bibliothek `garminconnect` genutzt; Garmin kann sie jederzeit blockieren oder ändern.

## Claude-Konnektor

In Claude unter *Einstellungen → Konnektoren → Benutzerdefinierten Konnektor hinzufügen* die Konnektor-URL eintragen
(steht auch im Dashboard unter „Claude-Konnektor einrichten“). 14 Werkzeuge: Überblick, Plan lesen/ändern/ersetzen, Status setzen,
Prognose, Z2-Verlauf, Gesundheit, Aktivitäten, Einstellungen (Ziel, Renndatum, HF-Band), Bestleistung eintragen, Sync auslösen.
Beispiel: „Ersetze meinen Plan für die nächsten 4 Wochen passend zu meiner Form und meinem Ziel.“

## Betrieb

| Aufgabe | Befehl |
|---|---|
| Status / Logs | `systemctl status training-dashboard`, `journalctl -u training-dashboard -f` |
| Update | neues Projekt entpacken, `sudo ./deploy/install.sh` |
| Backup | täglich 03:30 nach `/var/backups/training-dashboard` (14 Tage); manuell `sudo training-dashboard-backup` |
| Wiederherstellen | Dienst stoppen, Backup nach `/var/lib/training-dashboard/training.db` kopieren (Besitzer `training`), Dienst starten |
| Einstellungen | `/etc/training-dashboard/env` (danach Neustart) |

Ziel (Standard 3:20:00) und Renndatum (Standard 04.04.2027) sind Platzhalter und per Konnektor (`update_settings`) änderbar.

## Berechnungen

- **Z2**: Herzfrequenzband (Standard 137–149), erste 5 Minuten, Stillstand und Rollphasen ausgenommen, mindestens 10 Minuten; Wochenmittel, Trend (lineare Regression, letzte ≤12 Wochen, ≥4 nötig).
- **Belastung**: HF-basiert (1 h bei Schwellen-HF ≈ 100), CTL 42 Tage, ATL 7 Tage, TSB als Formanzeige.
- **Prognose**: Riegel-Exponent 1,06 aus den besten 5k/10k/Halbmarathon-Zeiten der letzten 240 Tage; Szenarien potenziell / realistisch / konservativ; Z2-Trend gedämpft und auf ±5 % begrenzt. Das ist eine Schätzung, keine Garantie.
- **Plan-Abgleich**: Einheit gilt als erledigt, wenn ≥ 60 % der geplanten km oder Minuten aufgezeichnet wurden; doppelt aufgezeichnete Einheiten werden nur einmal gezählt.

## Grenzen (ehrlich)

- Strava- und Garmin-Anbindung sind nur gegen Testattrappen geprüft, nicht gegen die echten Dienste (Garmin-Feldnamen können abweichen).
- Das Installationsskript wurde per Syntaxprüfung und Dry-Run getestet, aber nicht auf einem echten Ubuntu-Server ausgeführt.

## Entwicklung

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py"    # Unit-Tests
python tests/server_e2e.py                              # Server + Passwortschutz + alle MCP-Werkzeuge
```
