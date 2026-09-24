#!/usr/bin/env bash
# Argus private PKI and service identities.
#
# Creates, under deploy/pki/out/ (git-ignored, never commit it):
#
#   ca/ca.pem, ca/ca.key              private CA (EC P-256), 5 years
#   <svc>/ca.pem                      CA certificate (trust anchor)
#   <svc>/tls.pem, <svc>/tls.key      mTLS certificate for the service's
#                                     internal listener and outbound calls
#                                     (CN=<svc>, serverAuth+clientAuth), 1 year
#   <svc>/service.key                 Ed25519 key signing service tokens
#   <svc>/peers/<peer>.pub.pem        public service keys of its peers
#   surveillance/auth.key             Ed25519 key signing user access tokens
#   <svc>/camera-secrets.key          AES-256 key sealing camera credentials in
#                                     the database (surveillance, parking).
#                                     BACK IT UP: it is never regenerated, and
#                                     without it stored camera URLs are lost.
#   gateway/tls.pem, gateway/tls.key  client cert the public gateway uses to
#                                     reach parking over mTLS (CN=gateway)
#   public/fullchain.pem, privkey.pem self-signed placeholder for the public
#                                     listener until scripts/setup-tls.sh
#                                     installs a real certificate
#
# Each <svc>/ directory is the complete secret bundle for that machine: copy
# it to /run/secrets/argus/<svc>/ there (mode 700 dir, 600 files).
#
# Usage:
#   deploy/pki.sh init                      CA + all bundles (idempotent)
#   deploy/pki.sh renew <svc>               re-issue one service's TLS cert
#   SURVEILLANCE_SANS="DNS:edge,DNS:surv.lan,IP:10.0.0.5" deploy/pki.sh init
#
# SANs default to the Docker service names used by the compose files. Set
# <SVC>_SANS to add the hostnames/IPs peers use to reach each machine.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="${ARGUS_PKI_OUT:-$ROOT/pki/out}"
SERVICES=(surveillance parking face)
DAYS_CA=1825
DAYS_LEAF=365

declare -A DEFAULT_SANS=(
  [surveillance]="DNS:surveillance,DNS:surveillance-internal,DNS:edge,DNS:localhost"
  [parking]="DNS:parking,DNS:parking-internal,DNS:parking-edge,DNS:edge,DNS:localhost"
  [face]="DNS:face,DNS:face-internal,DNS:face-edge,DNS:edge,DNS:localhost"
  [gateway]="DNS:gateway"
)

# Which peers' public service keys each service trusts.
declare -A PEERS=(
  [surveillance]="parking face"
  [parking]="surveillance"
  [face]="surveillance parking"
)

log() { printf '==> %s\n' "$*"; }

need() { command -v "$1" >/dev/null || { echo "missing dependency: $1" >&2; exit 1; }; }

init_ca() {
  mkdir -p "$OUT/ca"
  if [[ -f "$OUT/ca/ca.key" ]]; then
    log "CA exists, keeping"
    return
  fi
  log "Creating private CA"
  openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/ca/ca.key"
  openssl req -x509 -new -key "$OUT/ca/ca.key" -sha256 -days "$DAYS_CA" \
    -subj "/O=Argus/CN=Argus Internal CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "$OUT/ca/ca.pem"
}

issue_tls() {
  local name="$1" dir="$OUT/$1"
  local var
  var="$(echo "$name" | tr '[:lower:]' '[:upper:]')_SANS"
  local sans="${!var:-${DEFAULT_SANS[$name]}}"
  mkdir -p "$dir"
  log "Issuing TLS certificate for $name ($sans)"
  openssl ecparam -name prime256v1 -genkey -noout -out "$dir/tls.key"
  openssl req -new -key "$dir/tls.key" -subj "/O=Argus/CN=$name" -out "$dir/tls.csr"
  openssl x509 -req -in "$dir/tls.csr" -CA "$OUT/ca/ca.pem" -CAkey "$OUT/ca/ca.key" \
    -CAcreateserial -days "$DAYS_LEAF" -sha256 \
    -extfile <(printf '%s\n' \
      "basicConstraints=critical,CA:FALSE" \
      "keyUsage=critical,digitalSignature,keyAgreement" \
      "extendedKeyUsage=serverAuth,clientAuth" \
      "subjectAltName=$sans") \
    -out "$dir/tls.pem"
  rm -f "$dir/tls.csr"
  cp "$OUT/ca/ca.pem" "$dir/ca.pem"
}

ed25519_key() {
  local file="$1"
  if [[ -f "$file" ]]; then
    return
  fi
  openssl genpkey -algorithm ed25519 -out "$file"
}

bundle() {
  local svc="$1" dir="$OUT/$1"
  mkdir -p "$dir/peers"
  [[ -f "$dir/tls.pem" ]] || issue_tls "$svc"
  ed25519_key "$dir/service.key"
  openssl pkey -in "$dir/service.key" -pubout -out "$dir/service.pub.pem"
  if [[ "$svc" == surveillance ]]; then
    ed25519_key "$dir/auth.key"
    openssl pkey -in "$dir/auth.key" -pubout -out "$dir/auth.pub.pem"
  fi
  if [[ "$svc" == surveillance || "$svc" == parking ]] && [[ ! -f "$dir/camera-secrets.key" ]]; then
    log "Creating camera-secrets key for $svc (back it up)"
    openssl rand -base64 32 >"$dir/camera-secrets.key"
  fi
}

distribute_peer_keys() {
  local svc peer
  for svc in "${SERVICES[@]}"; do
    rm -f "$OUT/$svc"/peers/*.pub.pem
    for peer in ${PEERS[$svc]}; do
      cp "$OUT/$peer/service.pub.pem" "$OUT/$svc/peers/$peer.pub.pem"
    done
  done
}

# Service containers run as uid/gid 1001 (see the Dockerfiles); the edge
# proxy's master process runs as root and can read everything.
SECRET_UID="${ARGUS_SECRET_UID:-1001}"

lock_down() {
  cp "$OUT/ca/ca.pem" "$OUT/ca.pem"
  find "$OUT" -type d -exec chmod 700 {} +
  find "$OUT" -type f -exec chmod 600 {} +
  chmod 644 "$OUT/ca.pem"
  if [[ "$(id -u)" == 0 ]]; then
    local svc
    for svc in "${SERVICES[@]}"; do
      chown -R "$SECRET_UID:$SECRET_UID" "$OUT/$svc"
    done
  else
    echo "note: not root; chown service bundles to uid $SECRET_UID on the target machines" >&2
  fi
}

public_placeholder() {
  local dir="$OUT/public" name="${PUBLIC_SERVER_NAME:-localhost}"
  [[ -f "$dir/fullchain.pem" ]] && return
  mkdir -p "$dir"
  log "Creating self-signed placeholder certificate for $name"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
    -keyout "$dir/privkey.pem" -out "$dir/fullchain.pem" -days 30 \
    -subj "/CN=$name" -addext "subjectAltName=DNS:$name" 2>/dev/null
}

cmd_init() {
  need openssl
  init_ca
  public_placeholder
  local svc
  for svc in "${SERVICES[@]}"; do
    bundle "$svc"
  done
  [[ -f "$OUT/gateway/tls.pem" ]] || issue_tls gateway
  distribute_peer_keys
  lock_down
  log "Done. Bundles in $OUT/<service>/ (copy each to its machine)."
}

cmd_renew() {
  local svc="${1:?service name required}"
  need openssl
  [[ -f "$OUT/ca/ca.key" ]] || { echo "no CA; run init first" >&2; exit 1; }
  issue_tls "$svc"
  lock_down
  log "Renewed $svc TLS certificate; reload its edge proxy."
}

case "${1:-init}" in
  init) cmd_init ;;
  renew) shift; cmd_renew "$@" ;;
  *) echo "usage: $0 [init|renew <service>]" >&2; exit 2 ;;
esac
