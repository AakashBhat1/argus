# AWS Deployment Security Checklist â€” items NOT fixable in code

Companion to `plans/argus-threat-model.md` in the repo. Every threat in that model was verified against the codebase on 2026-07-05. Everything that could be fixed in code **has been fixed** (see "Already fixed in code" at the bottom). The items below can only be completed at deploy time on AWS, or need an architectural decision first. Work through them top-to-bottom before exposing the EC2 instance to the internet.

---

## 1. HTTPS / TLS on Nginx â€” TM-001 (ðŸ”´ highest priority)

Everything (login credentials, JWTs, live video WS frames) currently travels over plain HTTP on port 80.

- [ ] Point a domain (or subdomain) at the EC2 Elastic IP.
- [ ] Install certbot on the instance and issue a Let's Encrypt cert, **or** put an ALB with an ACM certificate in front and keep Nginx on HTTP inside the VPC.
- [ ] Add a `listen 443 ssl;` server block to `nginx/nginx.conf` and a port-80 block that only does `return 301 https://$host$request_uri;`.
- [ ] Add `add_header Strict-Transport-Security "max-age=31536000" always;` once HTTPS works.
- [ ] Update `NEXT_PUBLIC_API_URL` / WebSocket URL scheme (`wss://`) in the frontend build args.

## 2. EC2 Security Group lockdown â€” TM-002 / TM-003 (ðŸ”´)

Only expose what the browser and the relay actually need:

| Port | Service | Rule |
| --- | --- | --- |
| 80/443 | Nginx | Open to 0.0.0.0/0 (443 only once TLS is live) |
| 8554 | MediaMTX RTSP ingest | **Restrict to your home network's public IP** (or a WireGuard/Tailscale tunnel). Publish now requires a password (`mtx_publisher`), but IP-scoping is defence-in-depth. |
| 8889 / 8189udp | MediaMTX WebRTC | Needed by browsers for playback â†’ open, but see item 3. |
| 3001, 8000, 9997, 5432 | frontend, backend, MediaMTX API, Postgres | **Close.** Nginx proxies 3001/8000 internally; 9997/5432 must never be public. Remove the host port mappings for 9997 (and ideally 3001/8000) from `docker-compose.yml` on the server, or bind them to 127.0.0.1 like Postgres already is. |

## 3. MediaMTX read (playback) access is still anonymous â€” TM-003 residual

Publishing now requires credentials, but **anyone who can reach port 8889 can watch the streams** (path names are guessable: `live/cam1`). Options, pick one at deploy time:

1. **MediaMTX JWT auth** (`authMethod: jwt` + `authJWTJWKS`) â€” backend already issues JWTs; MediaMTX can validate them via a JWKS endpoint. Cleanest, but needs a small backend endpoint exposing the signing key as JWKS (requires switching JWT signing from HS256 to RS256/ES256).
2. **Proxy WebRTC through Nginx** and gate `/mediamtx/` locations with `auth_request` against the backend.
3. **IP-restrict 8889** in the security group if only known networks ever view the dashboard.

## 4. Secrets â€” set real values on the server (ðŸ”´)

The compose file now reads all of these from the environment; the in-repo defaults are dev-only placeholders. On EC2, create a root-level `.env` next to `docker-compose.yml` with strong random values:

```
POSTGRES_PASSWORD=<random 32+ chars>
MEDIAMTX_API_PASSWORD=<random>
MEDIAMTX_PUBLISH_PASSWORD=<random>
```

And in `backend/yolo_classifier/.env`:

```
SECRET_KEY=<random 64 chars>          # backend refuses to boot with the placeholder when DEBUG=false
ACCESS_TOKEN_EXPIRE_MINUTES=1440      # or lower
```

On the relay laptop (never committed):

```powershell
$env:ARGUS_RELAY_SOURCE = "rtsp://<cam-lan-url-with-creds>"
$env:ARGUS_RELAY_DEST   = "rtsp://mtx_publisher:<MEDIAMTX_PUBLISH_PASSWORD>@<domain>:8554/live/cam1"
```

- [ ] Rotate the camera password and the previously committed `mtx_api_change_me` value â€” both have lived in git history.
- [ ] The old EC2 IP `54.173.227.197` is in git history via the relay script; treat it as public knowledge or move to a new Elastic IP.

## 5. JWT storage & refresh tokens â€” TM-004 / TM-016 (needs an architecture decision, not a config)

Deliberately **not** changed in code this pass:

- Tokens live in `localStorage` (`frontend/src/lib/auth.ts`). Moving to `HttpOnly` cookies would neutralise XSS token theft, but the WebSocket handshake authenticates via `?token=` query param, which requires a JS-readable token. Migrating means: backend sets an `HttpOnly` cookie on login + CSRF protection + a cookie-based WS handshake (read cookie during the WS upgrade). ~1 day of coordinated backend+frontend work.
- Token lifetime is now configurable and down from 7 days to 24 h, but stateless JWTs still can't be revoked. Proper fix is short-lived access tokens (15â€“30 min) + rotating refresh tokens stored server-side. Do this together with the cookie migration.

## 6. Sync telemetry writers skip non-Postgres databases â€” TM-018 (accepted dev-only limitation)

`app/detection/events.py` and `app/services/intent_persistence.py` write via raw psycopg2 for speed on the hot inference path and intentionally no-op on SQLite (they log a warning once). This is only reachable when running local dev on SQLite or when `ALLOW_DB_FALLBACK=true`; `init_db` now refuses the silent SQLite fallback in production. **Rule for AWS: always run Postgres (container or RDS) and keep `ALLOW_DB_FALLBACK=false`.** Refactoring the writers to SQLAlchemy would add per-write overhead for no production benefit â€” don't, unless SQLite persistence is ever actually required.

## 7. Zones are a global JSON file â€” TM-013 residual

Zone writes are now admin-only, but zones live in one `roi_zones.json` shared by all tenants â€” an admin of tenant A technically edits geometry that also applies to tenant B's cameras with the same IDs. True per-tenant zones require moving zones into the DB with a `tenant_id` column. Fine for single-tenant deployments; schedule the DB migration before onboarding a second tenant.

## 8. Monitoring / detection (from the threat model's detection ideas)

- [ ] CloudWatch agent or `docker logs` shipping for the backend; alert on repeated `401`s on `/api/v1/auth/token` (brute force) and on Nginx `429` spikes.
- [ ] Alert on MediaMTX publisher session changes (a new publisher IP on `live/cam1` = possible fake-stream injection).
- [ ] Postgres: log failed logins; alert on connections from unexpected hosts.
- [ ] EBS snapshots / `pg_dump` cron for the `postgres_data` volume (intrusion-log integrity & availability objective).

---

## Verification verdicts (2026-07-05)

Legit and **fixed in code this pass**: TM-005 (nginx rate limiting), TM-007 (relay creds via env + log redaction), TM-011 (tenant filters in alerts/detections/intents **and analytics**, tenant stamped on alert creation, tenant inheritance on user creation), TM-012 (metrics behind auth), TM-013 (zone writes admin-only), TM-016 (token lifetime 7d â†’ 24h, env-configurable), TM-017 (writer pools 8/4 â†’ 16/8, env-tunable), TM-002/TM-003 partial (compose/MediaMTX secrets parametrised, publish requires auth).

Legit but **already fixed before this pass** (uncommitted working tree): TM-006 (SSRF default off), TM-008 (async pool 20+10), TM-009 (`stop-all` admin-gated), TM-010 (stream status tenant-filtered), TM-014 (WS tenant segregation + camera-ownership check on subscribe), TM-015 (pipeline stamps `tenant_id`), TM-018 partial (no silent SQLite fallback).

**Exaggerated / partially bogus claims** in the threat model: TM-017's "unhandled PoolError" â€” both writers catch all exceptions and rate-limit-log them; the real (now mitigated) issue was silent drops on pool exhaustion. TM-011 listed zones as SQL-backed â€” they're a JSON file, so "tenant_id filtering in SQL" doesn't apply (see item 7). TM-009/TM-010/TM-014/TM-015 described code that had already been fixed.

Deploy-time only: TM-001 (TLS), TM-004 (cookie/refresh architecture), plus the security-group and MediaMTX-read items above.
