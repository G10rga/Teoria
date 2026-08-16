#!/usr/bin/env bash
# Native Ubuntu install: local Postgres + gunicorn on 127.0.0.1:8012.
# Public HTTPS is Cloudflare Tunnel (not nginx). Run from the repo as root:
#
#   sudo ./deploy/setup-ubuntu.sh
#
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${TEORIA_USER:-teoria}"
APP_HOME="${TEORIA_HOME:-/opt/teoria}"
DB_NAME="${TEORIA_DB_NAME:-teoria}"
DB_USER="${TEORIA_DB_USER:-teoria}"
PORT="${TEORIA_PORT:-8012}"
DOMAIN="${TEORIA_DOMAIN:-teoria.g1orga.dev}"

systemctl stop teoria.service 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-contrib git rsync

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${APP_HOME}" --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

mkdir -p "${APP_HOME}"
if [[ "${REPO}" != "${APP_HOME}" ]]; then
  rsync -a --delete --exclude '.venv' --exclude '.git' --exclude '*.db' --exclude 'html_cache' \
    --exclude '.env' \
    "${REPO}/" "${APP_HOME}/"
  if [[ -d "${REPO}/.git" ]]; then
    rsync -a "${REPO}/.git" "${APP_HOME}/"
  fi
fi
chown -R "${APP_USER}:${APP_USER}" "${APP_HOME}"

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_HOME}/static/tickets"

if [[ ! -d "${APP_HOME}/.venv" ]]; then
  sudo -u "${APP_USER}" python3 -m venv "${APP_HOME}/.venv"
fi
sudo -u "${APP_USER}" "${APP_HOME}/.venv/bin/pip" install --upgrade pip
sudo -u "${APP_USER}" "${APP_HOME}/.venv/bin/pip" install -r "${APP_HOME}/requirements.txt"

systemctl enable --now postgresql

if [[ -f "${APP_HOME}/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  # shellcheck source=/dev/null
  source "${APP_HOME}/.env"
  set +a
  PORT="${TEORIA_PORT:-$PORT}"
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  DB_PASS="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  ELSE
    ALTER ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL
  sudo -u postgres psql -d "${DB_NAME}" -v ON_ERROR_STOP=1 <<SQL
GRANT ALL ON SCHEMA public TO ${DB_USER};
ALTER SCHEMA public OWNER TO ${DB_USER};
SQL
  DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}"
fi

if [[ ! -f "${APP_HOME}/.env" ]]; then
  SECRET_KEY="${SECRET_KEY:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')}"
  install -o "${APP_USER}" -g "${APP_USER}" -m 600 /dev/null "${APP_HOME}/.env"
  cat > "${APP_HOME}/.env" <<EOF
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=${SECRET_KEY}
DATABASE_URL=${DATABASE_URL}
TEORIA_PORT=${PORT}
TEORIA_SECURE_COOKIES=1
PREFERRED_URL_SCHEME=https
EOF
fi

UNIT="/etc/systemd/system/teoria.service"
sed \
  -e "s|/opt/teoria|${APP_HOME}|g" \
  -e "s|User=teoria|User=${APP_USER}|g" \
  -e "s|Group=teoria|Group=${APP_USER}|g" \
  "${APP_HOME}/deploy/teoria.service" > "${UNIT}"

sed -i "s/--bind 127.0.0.1:8012/--bind 127.0.0.1:${PORT}/" "${UNIT}"

if ss -lnt | awk '{print $4}' | grep -qE "[:.]${PORT}\$"; then
  echo "port ${PORT} is already in use. Set TEORIA_PORT to a free loopback port (not 8000/8001)." >&2
  ss -lntp | grep -E "[:.]${PORT}\b" || true
  exit 1
fi

systemctl daemon-reload
systemctl enable --now teoria.service

echo
echo "Teoria is listening on 127.0.0.1:${PORT}"
echo "Check: curl -sS http://127.0.0.1:${PORT}/health"
echo
echo "Cloudflare Tunnel — add this hostname (keep your other ingress rules):"
echo "  hostname: ${DOMAIN}"
echo "  service:  http://127.0.0.1:${PORT}"
echo
echo "Then:"
echo "  sudo cloudflared tunnel route dns <TUNNEL_NAME> ${DOMAIN}"
echo "  sudo systemctl restart cloudflared"
echo
echo "Import the ticket bank once (from this server):"
echo "  sudo -u ${APP_USER} bash -lc 'cd ${APP_HOME} && set -a && . ./.env && set +a && .venv/bin/python scraper.py scrape --pages 47 --delay 1.0'"
echo
echo "Application files: ${APP_HOME}"
echo "Secrets file:      ${APP_HOME}/.env"
