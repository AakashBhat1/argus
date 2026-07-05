#!/usr/bin/env bash
# Issue a Let's Encrypt certificate and switch nginx to HTTPS.
#
# Prerequisites (run on the EC2 host from the repo root):
#   - A DNS A record for <domain> pointing at this host's Elastic IP
#   - Ports 80 and 443 open in the security group
#   - The stack running:  docker compose up -d
#
# Usage:  ./scripts/setup-tls.sh <domain> <email>
set -euo pipefail

DOMAIN="${1:?usage: setup-tls.sh <domain> <email>}"
EMAIL="${2:?usage: setup-tls.sh <domain> <email>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking that $DOMAIN resolves..."
if ! getent hosts "$DOMAIN" >/dev/null; then
  echo "ERROR: $DOMAIN does not resolve. Create the DNS A record first." >&2
  exit 1
fi

echo "==> Requesting certificate for $DOMAIN (HTTP-01 via nginx webroot)..."
docker compose run --rm certbot certonly \
  --webroot --webroot-path /var/www/certbot \
  --domain "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos --no-eff-email --non-interactive

echo "==> Switching nginx to the HTTPS config..."
sed "s/YOUR_DOMAIN/${DOMAIN}/g" nginx/nginx-ssl.conf > nginx/nginx.conf

echo "==> Validating and reloading nginx..."
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload

echo "==> Setting MEDIAMTX_PUBLIC_HOST=$DOMAIN in .env..."
if grep -q '^MEDIAMTX_PUBLIC_HOST=' .env 2>/dev/null; then
  sed -i "s/^MEDIAMTX_PUBLIC_HOST=.*/MEDIAMTX_PUBLIC_HOST=${DOMAIN}/" .env
else
  echo "MEDIAMTX_PUBLIC_HOST=${DOMAIN}" >> .env
fi
docker compose up -d mediamtx

cat <<EOF

Done. https://${DOMAIN} is live (port 80 now redirects).

Add automatic renewal (certs expire after 90 days), e.g. in crontab -e:

  0 4 * * 1 cd ${REPO_ROOT} && docker compose run --rm certbot renew --webroot --webroot-path /var/www/certbot && docker compose exec nginx nginx -s reload

Remaining manual steps:
  - Security group: close 3001/8000/9997; keep 80/443 (public), 8554
    (your home IP only), 8189/udp (public), 22 (your IP only).
  - Update the relay destination on the laptop to use ${DOMAIN}.
EOF
