#!/usr/bin/env bash
# Generate strong random secrets for a production deployment.
#
# Creates/updates the root .env (compose secrets) and replaces the
# placeholder SECRET_KEY in backend/yolo_classifier/.env. Idempotent:
# values that are already set to something non-placeholder are kept.
#
# Usage (on the EC2 host, from the repo root):  ./scripts/generate-secrets.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

rand() { openssl rand -hex "$1"; }

ensure_env_var() {
  # ensure_env_var <file> <key> <value> — append if missing, replace if placeholder
  local file="$1" key="$2" value="$3"
  touch "$file"
  if grep -q "^${key}=" "$file"; then
    local current
    current="$(grep "^${key}=" "$file" | head -1 | cut -d= -f2-)"
    case "$current" in
      ""|*change_me*|*changeme*|*placeholder*|surveillance)
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
        echo "  ${key}: replaced placeholder" ;;
      *)
        echo "  ${key}: already set, keeping" ;;
    esac
  else
    echo "${key}=${value}" >> "$file"
    echo "  ${key}: generated"
  fi
}

echo "==> Root .env (docker compose secrets)"
ensure_env_var .env POSTGRES_PASSWORD          "$(rand 24)"
ensure_env_var .env MEDIAMTX_API_PASSWORD      "$(rand 16)"
ensure_env_var .env MEDIAMTX_PUBLISH_PASSWORD  "$(rand 16)"
# Public IP/domain advertised in WebRTC ICE candidates; setup-tls.sh sets
# this to the domain. Until then, default to this host's public IP.
if ! grep -q '^MEDIAMTX_PUBLIC_HOST=' .env; then
  PUBLIC_IP="$(curl -fs --max-time 5 http://checkip.amazonaws.com || true)"
  [ -n "$PUBLIC_IP" ] && ensure_env_var .env MEDIAMTX_PUBLIC_HOST "$PUBLIC_IP" \
    || echo "  MEDIAMTX_PUBLIC_HOST: could not autodetect, set it manually"
fi
chmod 600 .env

BACKEND_ENV="backend/yolo_classifier/.env"
echo "==> Backend env ($BACKEND_ENV)"
if [ ! -f "$BACKEND_ENV" ] && [ -f "backend/yolo_classifier/.env.example" ]; then
  cp backend/yolo_classifier/.env.example "$BACKEND_ENV"
  echo "  created from .env.example"
fi
ensure_env_var "$BACKEND_ENV" SECRET_KEY "$(rand 32)"
ensure_env_var "$BACKEND_ENV" ACCESS_TOKEN_EXPIRE_MINUTES "1440"
chmod 600 "$BACKEND_ENV"

cat <<'EOF'

Done. Now:
  docker compose up -d --force-recreate
so every service picks up the new values.

The MediaMTX publish password changed — update the relay laptop:
  $env:ARGUS_RELAY_DEST = "rtsp://mtx_publisher:<MEDIAMTX_PUBLISH_PASSWORD>@<host>:8554/live/cam1"
(read the value from .env on this server; never commit it)
EOF
