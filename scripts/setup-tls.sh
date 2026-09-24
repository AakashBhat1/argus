#!/usr/bin/env bash
# Issue a Let's Encrypt certificate for the public gateway.
#
# Prerequisites (on the gateway / surveillance host, from the repo root):
#   - DNS A record for <domain> pointing at this host
#   - Ports 80 and 443 open; the stack running with the self-signed
#     placeholder from deploy/pki.sh
#
# Usage:  scripts/setup-tls.sh <domain> <email> [compose-file]
set -euo pipefail

DOMAIN="${1:?usage: setup-tls.sh <domain> <email> [compose-file]}"
EMAIL="${2:?usage: setup-tls.sh <domain> <email> [compose-file]}"
COMPOSE_FILE="${3:-docker-compose.yml}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
compose() { docker compose --env-file .env -f "$COMPOSE_FILE" "$@"; }

# The domain lands in .env and a sed replacement below.
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "ERROR: invalid domain: $DOMAIN" >&2
  exit 1
fi

if ! getent hosts "$DOMAIN" >/dev/null; then
  echo "ERROR: $DOMAIN does not resolve. Create the DNS A record first." >&2
  exit 1
fi

echo "==> Requesting certificate for $DOMAIN (HTTP-01 via the edge webroot)"
compose run --rm certbot certonly \
  --webroot --webroot-path /var/www/certbot \
  --domain "$DOMAIN" --email "$EMAIL" \
  --agree-tos --no-eff-email --non-interactive

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

echo "==> Pointing the public listener at the new certificate"
set_env PUBLIC_SERVER_NAME "$DOMAIN"
set_env PUBLIC_TLS_CERT "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
set_env PUBLIC_TLS_KEY "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
set_env MEDIAMTX_PUBLIC_HOST "$DOMAIN"
chmod 600 .env

compose up -d --force-recreate edge mediamtx
compose exec edge nginx -t

cat <<EOT
==> Done. Add a renewal cron entry, e.g.:
  0 3 * * * cd $REPO_ROOT && docker compose --env-file .env -f $COMPOSE_FILE run --rm certbot renew --quiet && docker compose --env-file .env -f $COMPOSE_FILE exec edge nginx -s reload
EOT
