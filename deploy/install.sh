#!/usr/bin/env bash
# Installation / Update des Training-Dashboards auf Ubuntu 22.04 oder 24.04 (systemd + Caddy mit automatischem HTTPS).
#
#   sudo ./deploy/install.sh --domain training.example.com --email du@example.com
#   sudo ./deploy/install.sh            # erneut ausführen = Update (Domain und Passwörter bleiben)
#
# Optionen: --domain D  --email E  --user NAME (Anmeldename, Standard: trainer)  --no-firewall  --dry-run  -h
set -euo pipefail

APP_DIR=/opt/training-dashboard
ENV_DIR=/etc/training-dashboard
ENV_FILE=$ENV_DIR/env
DATA_DIR=/var/lib/training-dashboard
BACKUP_DIR=/var/backups/training-dashboard
SVC_USER=training
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN=""; EMAIL=""; DASH_USER_ARG=""; FIREWALL=1; DRY=0

usage() { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { echo "FEHLER: $*" >&2; exit 1; }
info() { echo "==> $*"; }
run() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
# Datei schreiben: put <pfad> <modus> <owner:group>   (Inhalt über stdin)
put() {
  if [ "$DRY" = 1 ]; then echo "[dry-run] schreibe $1 (Modus $2)"; cat >/dev/null; return; fi
  install -d -m 755 "$(dirname "$1")"; cat >"$1.tmp"; chmod "$2" "$1.tmp"; chown "$3" "$1.tmp"; mv "$1.tmp" "$1"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --user) DASH_USER_ARG="${2:-}"; shift 2 ;;
    --no-firewall) FIREWALL=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unbekannte Option: $1 (siehe --help)" ;;
  esac
done

# ---------------------------------------------------------------- Prüfungen
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || die "Bitte als root starten: sudo $0 ..."
[ -f "$SRC_DIR/app/main.py" ] || die "app/main.py nicht gefunden – Skript muss aus dem entpackten Projekt laufen."
if [ -r /etc/os-release ]; then
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ]; then die "Nur Ubuntu wird unterstützt (gefunden: ${ID:-unbekannt})."; fi
  case "${VERSION_ID:-0}" in 22.04|24.04|26.04) ;; *) [ "$DRY" = 1 ] || die "Ubuntu 22.04 oder 24.04 erforderlich (gefunden: ${VERSION_ID:-?})." ;; esac
fi
command -v systemctl >/dev/null || [ "$DRY" = 1 ] || die "systemd wird benötigt."

if [ -f "$ENV_FILE" ]; then
  [ -n "$DOMAIN" ] || DOMAIN="$(grep -E '^BASE_URL=' "$ENV_FILE" | head -1 | sed -E 's#^BASE_URL=https?://##; s#/.*##')"
fi
[ -n "$DOMAIN" ] || die "--domain fehlt (z. B. --domain training.example.com)."
echo "$DOMAIN" | grep -Eq '^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$' || die "Ungültige Domain: $DOMAIN"
[ -z "$EMAIL" ] || echo "$EMAIL" | grep -Eq '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' || die "Ungültige E-Mail: $EMAIL"
[ -z "$DASH_USER_ARG" ] || echo "$DASH_USER_ARG" | grep -Eq '^[A-Za-z0-9._-]{1,32}$' || die "Ungültiger Benutzername (erlaubt: A-Z a-z 0-9 . _ -)"

# DNS-Hinweis (keine Abbruchbedingung)
if command -v getent >/dev/null; then
  if ! getent hosts "$DOMAIN" >/dev/null; then
    echo "HINWEIS: $DOMAIN löst noch nicht auf. Ohne A-Record auf diese Server-IP kann kein HTTPS-Zertifikat ausgestellt werden."
  fi
fi

# ---------------------------------------------------------------- Pakete
info "Systempakete installieren"
export DEBIAN_FRONTEND=noninteractive
run apt-get update -y
run apt-get install -y python3 python3-venv python3-pip ca-certificates curl gpg apt-transport-https rsync openssl sqlite3 cron
[ "$FIREWALL" = 0 ] || run apt-get install -y ufw

if ! command -v caddy >/dev/null 2>&1; then
  info "Caddy (offizielles Paket-Repository) installieren"
  if [ "$DRY" = 1 ]; then
    echo "[dry-run] Caddy-Repository einrichten und installieren"
  else
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt >/etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
  fi
fi

# ---------------------------------------------------------------- Benutzer, Code, venv
info "Dienstbenutzer und Anwendung"
if [ "$DRY" = 1 ] || ! id "$SVC_USER" >/dev/null 2>&1; then
  run useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
fi
run install -d -m 755 "$APP_DIR"
run rsync -a --delete --exclude '__pycache__' --exclude '.venv' --exclude 'data' --exclude '.git' --exclude '*.zip' \
  "$SRC_DIR/app" "$SRC_DIR/requirements.txt" "$SRC_DIR/deploy" "$APP_DIR/"
run install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR"
run install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$BACKUP_DIR"
[ -d "$APP_DIR/.venv" ] || run python3 -m venv "$APP_DIR/.venv"
run "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
run "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
run "$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/app"
run chown -R root:root "$APP_DIR"

# ---------------------------------------------------------------- Konfiguration (Geheimnisse nur beim ersten Mal)
info "Konfiguration $ENV_FILE"
FIRST_RUN=0
if [ ! -f "$ENV_FILE" ]; then
  FIRST_RUN=1
  DASH_PASSWORD="$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)"
  MCP_SECRET="$(openssl rand -hex 24)"
  put "$ENV_FILE" 640 "root:$SVC_USER" <<ENVEOF
# Konfiguration des Training-Dashboards. Nach Änderungen: sudo systemctl restart training-dashboard
BASE_URL=https://$DOMAIN
DATA_DIR=$DATA_DIR
TZ=Europe/Berlin

# Anmeldung an der Webseite (HTTP Basic Auth)
DASH_USER=${DASH_USER_ARG:-trainer}
DASH_PASSWORD=$DASH_PASSWORD

# Geheimer Pfadteil der Claude-Konnektor-URL: https://$DOMAIN/mcp/<MCP_SECRET>
MCP_SECRET=$MCP_SECRET

# Strava: App unter https://www.strava.com/settings/api anlegen. Autorization Callback Domain = $DOMAIN
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=

# Garmin (optional, inoffizielle Schnittstelle). Erst-Anmeldung: sudo training-dashboard-garmin-login
GARMIN_EMAIL=
GARMIN_PASSWORD=

SYNC_INTERVAL_MIN=60
HISTORY_DAYS=450
SEED_PLAN=1
ENVEOF
else
  [ -z "$EMAIL$DASH_USER_ARG" ] || echo "HINWEIS: Bestehende Konfiguration wird nicht überschrieben. --email/--user wirken nur bei der Erstinstallation."
  if [ -n "$DOMAIN" ] && ! grep -q "^BASE_URL=https://$DOMAIN\$" "$ENV_FILE"; then
    run sed -i "s#^BASE_URL=.*#BASE_URL=https://$DOMAIN#" "$ENV_FILE"
  fi
fi

# ---------------------------------------------------------------- systemd
info "systemd-Dienst"
put /etc/systemd/system/training-dashboard.service 644 root:root <<UNIT
[Unit]
Description=Training Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header
Restart=on-failure
RestartSec=5
StateDirectory=training-dashboard
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=true
LockPersonality=true
RestrictRealtime=true
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
UNIT
run systemctl daemon-reload
run systemctl enable training-dashboard >/dev/null
run systemctl restart training-dashboard

# ---------------------------------------------------------------- Caddy (HTTPS)
info "Caddy (HTTPS für $DOMAIN)"
MARK="# managed by training-dashboard"
CADDYFILE=/etc/caddy/Caddyfile
caddy_block() {
  echo "$MARK"
  if [ -n "$EMAIL" ]; then printf '{\n\temail %s\n}\n' "$EMAIL"; fi
  cat <<CB
$DOMAIN {
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000"
		-Server
	}
	reverse_proxy 127.0.0.1:8000
}
CB
}
is_default_caddyfile() {   # leer oder die mitgelieferte Beispieldatei
  [ ! -s "$CADDYFILE" ] || grep -q "$MARK" "$CADDYFILE" || { grep -q '/usr/share/caddy' "$CADDYFILE" && [ "$(grep -Evc '^\s*(#|$)' "$CADDYFILE")" -le 8 ]; }
}
if [ "$DRY" = 1 ]; then
  echo "[dry-run] Caddyfile für $DOMAIN schreiben und neu laden"
elif is_default_caddyfile; then
  caddy_block | put "$CADDYFILE" 644 root:root
else
  echo "HINWEIS: Eine fremde /etc/caddy/Caddyfile existiert und bleibt unverändert."
  echo "         Block für dein Setup: /etc/caddy/training-dashboard.caddy (per 'import' einbinden)."
  caddy_block | put /etc/caddy/training-dashboard.caddy 644 root:root
fi
if [ "$DRY" = 0 ]; then
  caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1 || die "Caddy-Konfiguration ungültig (caddy validate --config $CADDYFILE)."
  systemctl enable caddy >/dev/null; systemctl reload caddy || systemctl restart caddy
fi

# ---------------------------------------------------------------- Firewall
if [ "$FIREWALL" = 1 ]; then
  info "Firewall (ufw)"
  SSH_PORT="$(ss -tlnp 2>/dev/null | awk '/sshd/ {n=split($4,a,":"); print a[n]}' | sort -u | head -1 || true)"
  SSH_PORT="${SSH_PORT:-22}"
  run ufw allow "$SSH_PORT/tcp"
  run ufw allow 80/tcp
  run ufw allow 443/tcp
  run ufw --force enable
else
  echo "HINWEIS: Firewall übersprungen (--no-firewall). Ports 80 und 443 müssen erreichbar sein."
fi

# ---------------------------------------------------------------- Backup und Hilfsskripte
info "Backup (täglich 03:30, 14 Tage) und Hilfsbefehle"
put /usr/local/sbin/training-dashboard-backup 755 root:root <<'BK'
#!/usr/bin/env bash
set -euo pipefail
DB=/var/lib/training-dashboard/training.db
OUT=/var/backups/training-dashboard
[ -f "$DB" ] || exit 0
STAMP="$(date +%Y%m%d-%H%M%S)"
runuser -u training -- sqlite3 "$DB" ".backup '$OUT/training-$STAMP.db'"
find "$OUT" -name 'training-*.db' -mtime +14 -delete
BK
put /etc/cron.d/training-dashboard-backup 644 root:root <<'CR'
30 3 * * * root /usr/local/sbin/training-dashboard-backup
CR
put /usr/local/sbin/training-dashboard-garmin-login 755 root:root <<GL
#!/usr/bin/env bash
# Einmalige interaktive Garmin-Anmeldung (inkl. MFA-Code). Danach: sudo systemctl restart training-dashboard
set -euo pipefail
cd $APP_DIR
set -a; . $ENV_FILE; set +a
exec runuser -u $SVC_USER -- env DATA_DIR="\$DATA_DIR" GARMIN_EMAIL="\$GARMIN_EMAIL" GARMIN_PASSWORD="\$GARMIN_PASSWORD" $APP_DIR/.venv/bin/python -m app.garmin_login
GL
put /usr/local/sbin/training-dashboard-info 755 root:root <<INFO
#!/usr/bin/env bash
set -euo pipefail
. $ENV_FILE
echo "Adresse:         \$BASE_URL"
echo "Benutzername:    \$DASH_USER"
echo "Passwort:        \$DASH_PASSWORD"
echo "Konnektor-URL:   \$BASE_URL/mcp/\$MCP_SECRET"
INFO
# training.db heißt in der App so? Name aus der Konfiguration übernehmen
if [ "$DRY" = 0 ] && ! grep -q 'training.db' "$APP_DIR/app/config.py"; then
  echo "HINWEIS: Datenbankname in config.py prüfen und im Backup-Skript anpassen."
fi

# ---------------------------------------------------------------- Prüfung
if [ "$DRY" = 1 ]; then echo; info "Dry-Run beendet, es wurde nichts verändert."; exit 0; fi
info "Funktionsprüfung"
OK=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then OK=1; break; fi; sleep 1
done
[ "$OK" = 1 ] || { journalctl -u training-dashboard -n 30 --no-pager; die "Dienst antwortet nicht."; }
CODE="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/)"
[ "$CODE" = 401 ] || die "Passwortschutz greift nicht (HTTP $CODE statt 401)."
echo "Dienst läuft, Passwortschutz aktiv."

echo
echo "=============================================================="
echo " Installation abgeschlossen: https://$DOMAIN"
if [ "$FIRST_RUN" = 1 ]; then
  echo " Anmeldung:   ${DASH_USER_ARG:-trainer} / $DASH_PASSWORD"
  echo " Konnektor:   https://$DOMAIN/mcp/$MCP_SECRET"
  echo " (jederzeit erneut anzeigen: sudo training-dashboard-info)"
fi
echo " Zugangsdaten und Strava/Garmin-Schlüssel: $ENV_FILE"
echo " Das Zertifikat wird beim ersten Aufruf automatisch ausgestellt (DNS muss auf diesen Server zeigen)."
echo "=============================================================="
