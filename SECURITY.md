# Security Policy

Argus processes camera streams and authentication data, so vulnerability reports may contain sensitive operational details. Do not report a suspected vulnerability in a public issue.

## Reporting a vulnerability

Private vulnerability reporting is not currently enabled for this repository. Open a public issue with only the title `Security contact request` and ask the maintainer to establish a private channel. Do not include technical details in that issue.

Once a private channel is established, share:

- The affected component and revision.
- Reproduction steps or a proof of concept.
- The likely impact and required preconditions.
- Any suggested mitigation.

Remove real credentials, private camera URLs, access tokens, customer data, and unnecessary surveillance footage from the report.

## Supported versions

Argus has no versioned releases yet. Security fixes, when available, are made against the current `main` branch.

---

## Security model in one page

The full threat model, with STRIDE per trust boundary and the file that implements each control, is in [docs/threat-model.md](docs/threat-model.md).

| Layer | Control | Where |
|---|---|---|
| Topology | Surveillance (identity provider, cameras, risk engine) and parking (gate OCR, bays, ParkBot) are separate services with separate databases. The public gateway is the only listener reachable from the internet. | `deploy/compose/*`, `deploy/nginx/templates/10-gateway.conf.template` |
| Service to service | Mutual TLS between the nginx edges (private CA, CN = service name), plus a short-lived Ed25519 service token bound to the client certificate's CN and scoped per route. `/internal/*` returns 404 on the public listener. | `deploy/pki.sh`, `argus_common/service_http.py`, `argus_common/tokens.py`, `argus_common/mesh.py` |
| Cross-service facts | Transactional outbox and idempotent inbox; a peer may only publish event types it owns, as itself. | `argus_common/events.py`, `app/routers/internal.py` |
| Browsers | httpOnly `__Host-` access cookie (10 min) and a rotating, single-use refresh cookie with reuse detection (a replayed token revokes the session). CSRF header plus Origin check on every cookie-authenticated write. WebSockets check Origin and close at token expiry. | `argus_common/web_auth.py`, `services/surveillance/app/services/sessions.py` |
| Users | EdDSA access tokens (surveillance is the identity provider, parking verifies through JWKS). DB-backed login limiter (per username and per IP; usernames and IPs stored as HMACs). First admin only via bootstrap token or `python -m app.cli.create_admin`. | `app/services/auth.py`, `app/services/login_attempts.py` |
| Roles | Admin-only: camera, zone and user changes, arm mode, grants, bay release, slot layout, ParkBot commands, audit log. The dashboard hides those controls from operators; the API refuses them regardless. | `require_admin` in both services |
| Camera credentials | Stream URLs sealed at rest (AES-256-GCM with AAD and key ids, fail closed), masked in every API response, and never written to the audit log. | `argus_common/secretbox.py`, `app/services/camera_secrets.py` |
| Video | MediaMTX 1.21.1 (digest-pinned), no anonymous read. Each WebRTC playback is authorised per camera by the owning service (nginx `auth_request`). WebRTC origin pinned to the public origin. | `mediamtx.yml`, gateway `/_auth/stream*` |
| LLM (ParkBot) | The model output can only *propose* a whitelisted, validated action. It runs after the same admin confirms it within 5 minutes; the proposal is single use and bound to that admin's tenant. | `services/parking/app/services/parking_assistant.py`, `parking_commands.py` |
| Accountability | `audit_events` in both services: who did what, from which address, written in the same transaction as the change. Admin-only `GET /api/v1/audit` and `/api/v1/parking/audit`; kept `AUDIT_RETENTION_DAYS` (365). | `argus_common/audit.py` |
| Abuse | Per-IP limits at the gateway (auth 10/min, API 30/s), camera snapshots 30/min per session, WebSockets capped at 200 per tenant and 20 per user, every container has `mem_limit` and `pids_limit`. | gateway template, `argus_common/websocket_hub.py`, compose files |
| Supply chain | CI: ruff, bandit, pip-audit (Python and torch), npm audit, gitleaks over the full history, CodeQL, Trivy on all three images, Dependabot. Base images pinned by digest. Model weights loaded with `weights_only=True` (torch ≥ 2.6) and the DeepSORT embedder's SHA-256 is pinned. | `.github/workflows/`, `argus_vision/tracker.py` |

## Keys to back up, and how to rotate them

Everything lives in `deploy/pki/out/<svc>/` on the machine that ran `deploy/pki.sh`, and is copied to `/run/secrets/argus/<svc>/` on each host (directory mode 700, files 600). **Back up `deploy/pki/out/` and both databases together**, encrypted, off the host.

| Key | Loss means | Rotation |
|---|---|---|
| `surveillance/auth.key` (signs user access tokens) | Everyone is signed out; new key from `pki.sh init`. Nothing is lost. | 1. Copy `auth.pub.pem` into the directory named by `AUTH_PREVIOUS_PUBLIC_KEYS_DIR` (for example `/run/secrets/argus/surveillance/previous-auth/2026-11.pub.pem`). 2. Delete `auth.key` and `auth.pub.pem`, then run `deploy/pki.sh init` (it only creates missing keys). 3. Copy the bundle to the host and restart surveillance. 4. Wait at least 20 minutes (15-minute Bearer tokens plus the 5-minute JWKS cache in parking), then remove the old public key and restart again. |
| `<svc>/service.key` (signs service tokens) | Peers reject the service's internal calls until they receive the new public key. | Delete `<svc>/service.key` and `<svc>/service.pub.pem`, run `deploy/pki.sh init` (it rebuilds every `peers/` directory), copy the bundles of **both** hosts, and restart both services. Calls queued in the outbox retry automatically. |
| `<svc>/camera-secrets.key` (seals camera URLs) | **Every stored camera URL is lost.** It cannot be regenerated. | 1. Move the old key into the directory named by `CAMERA_SECRETS_PREVIOUS_KEYS_DIR`. 2. Create the new one: `openssl rand -base64 32 > camera-secrets.key` (mode 600). 3. Restart: at startup the service re-seals every row under the new key. 4. Keep the old key until a backup taken after the restart exists. |
| `<svc>/tls.pem` / `tls.key`, `gateway/*` (mTLS, 1 year) | Edges cannot reach each other. | `deploy/pki.sh renew <svc>`, copy the bundle, and recreate that host's edge. |
| `ca/ca.key` (private CA, 5 years) | No new certificates can be issued. Keep it offline. | Re-run `init` in a fresh `out/`, redistribute every bundle, and recreate all edges. |
| Public TLS (Let's Encrypt) | The browser warns. | `scripts/setup-tls.sh <domain> <email>`; renewal is automatic (certbot). |
| MediaMTX passwords (`.env`) | Playback, publishing or the control API stops until the passwords match again. | Change `MEDIAMTX_*_PASSWORD` in `.env`, run `scripts/generate-secrets.sh` (it re-derives `MEDIAMTX_VIEWER_BASIC`), then `docker compose up -d --force-recreate mediamtx edge` (and the relays' publish credentials). |
| Database passwords (`.env`) | The services cannot connect. | `ALTER USER ... PASSWORD` in Postgres, update `.env`, recreate the service. |

Any secret that was ever committed, pasted into an issue, or found by gitleaks must be **rotated**. Removing it from history is not enough.

## Operator checklist

Before exposing a deployment:

- [ ] `DEBUG=false` in both services' `.env` (DEBUG allows ephemeral keys, plaintext camera secrets and anonymous bootstrap).
- [ ] `AUTH_BOOTSTRAP_TOKEN` unset after the first admin exists (or never set: use `python -m app.cli.create_admin`).
- [ ] `PUBLIC_SERVER_NAME` set in the root `.env` (TLS name and the MediaMTX WebRTC origin), and `CORS_ORIGINS=["https://<that name>"]` in both services' `.env`: the CSRF/Origin and WebSocket origin checks compare against it.
- [ ] Firewall, surveillance host: 80/443 from anywhere; 8554/tcp (RTSP publish) only from the relay addresses; 8189/udp (WebRTC ICE); 8443/tcp only from the parking host.
- [ ] Firewall, parking host: 8443/tcp only from the surveillance host; 8554/tcp from its relays; 8189/udp.
- [ ] Database ports are never published. Postgres traffic stays on the internal Docker network of its own host, unencrypted by design (4.11); a database never moves to another host without TLS.
- [ ] `/run/secrets/argus/<svc>/` is mode 700, files 600, owned by the service's uid.
- [ ] Backups: both databases and `deploy/pki/out/`, encrypted, tested by a restore.
- [ ] On hosts upgraded from the old single-container stack, run the MediaMTX check in [docs/upgrading.md](docs/upgrading.md#existing-live-host-mediamtx-advisory).
- [ ] Dependabot and CI security jobs green on `main`; Trivy findings reviewed.

## Logs and personal data (DPDP)

- The audit log stores usernames, source IPs and plates (needed for accountability under India's DPDP Act 2023). It is admin-only, tenant-scoped and purged after `AUDIT_RETENTION_DAYS`.
- Application logs still contain usernames and client IPs (failed-login warnings, the edge access log). The compose files already cap container logs by size (`json-file`, 20 MB × 5 per container); anything that ships logs off the host must keep them **30 days or less** and treat them as personal data. Hashing them at the source is tracked in [docs/REMAINING_WORK.md](docs/REMAINING_WORK.md).
- Login throttling stores only HMACs of usernames and IPs.
