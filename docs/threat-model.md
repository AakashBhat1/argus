# Argus threat model (three-service topology)

Status: 2026-11-08, end of Phase 4. Supersedes `plans/argus-threat-model.md`
(written for the pre-split single-container stack; its 18 findings are fixed).

## 1. System

```
 browser ──HTTPS──▶ gateway (nginx, surveillance host, the only public listener)
                      ├─▶ frontend (Next.js)                       same host
                      ├─▶ surveillance API  ──▶ surveillance-db    same host
                      ├─▶ MediaMTX (WebRTC, per-camera auth_request)
                      └─mTLS─▶ parking edge (nginx, parking host)
                                  └─▶ parking API ──▶ parking-db, parking MediaMTX, Ollama
 surveillance edge ◀──mTLS + service token──▶ parking edge     (events, internal API)
 camera relays ──RTSP publish (password)──▶ each host's MediaMTX
```

Three services: **surveillance** (identity provider, cameras, risk engine,
security console), **parking** (gate plate OCR, bays, sessions, ParkBot) and
the **frontend** dashboard. A fourth, **face** (Phase 6), is planned as an
internal-only service behind the same mesh; its threats are listed in §6 so
that the design starts from them.

## 2. Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Live and recorded video | Privacy of everyone on camera; evidence | MediaMTX (live), `video/` volumes |
| Camera credentials (RTSP URLs) | Direct access to the cameras on the LAN | sealed in `cameras.stream_url` (both DBs) |
| User identities, sessions | Control of the console, arm mode, grants | surveillance `users`, `auth_sessions` |
| Signing keys (auth, service, camera-secrets, TLS) | Forge users or services; decrypt camera URLs | `/run/secrets/argus/<svc>/` |
| Plates, vehicle profiles, parking sessions | Personal data (DPDP); movement history | parking DB |
| Audit log | Accountability; tampering hides abuse | `audit_events` (both DBs) |
| Face templates (Phase 6) | Biometric data: highest sensitivity under DPDP | future face DB |
| Availability of alerts | A missed intrusion is the core failure | risk engine, WebSocket hub |

## 3. Actors

- **Anonymous internet user**: reaches only the gateway on 80/443.
- **Operator**: signed-in dashboard user; reads everything in their tenant, can start/stop surveillance streams and handle alerts.
- **Admin**: everything an operator can, plus configuration (cameras, zones, users, arm mode, grants, bays, ParkBot commands, audit log).
- **Other tenant's user**: a legitimate user aiming at another tenant's data.
- **Compromised peer service**: e.g. the parking host taken over, trying to escalate into surveillance.
- **Network attacker** between hosts, or between a relay and MediaMTX.
- **Malicious content**: a crafted plate, camera name or chat message aimed at the LLM, the UI or the logs.
- **Insider with host access**: can read `/run/secrets` on that host (out of scope to prevent; in scope to limit and detect).

## 4. Trust boundaries: STRIDE → control → implementation

### B1. Browser → gateway

| STRIDE | Threat | Control | Implemented in |
|---|---|---|---|
| S | Stolen or forged session | httpOnly `__Host-` access cookie (10 min); single-use refresh cookie; reuse revokes the session family | `argus_common/web_auth.py`, `services/surveillance/app/services/sessions.py` |
| S | Credential stuffing, brute force | DB-backed limiter per username and per IP with progressive lockout; gateway `argus_auth` zone 10/min | `app/services/login_attempts.py`, `deploy/nginx/templates/10-gateway.conf.template` |
| S | A fresh deployment claimed by whoever arrives first | First admin only with `AUTH_BOOTSTRAP_TOKEN` or the shell CLI | `app/routers/auth.py` (`_bootstrap_allowed`), `app/cli/create_admin.py` |
| T | CSRF on cookie-authenticated writes | CSRF header plus Origin check against `CORS_ORIGINS` | `web_auth.check_same_origin`, `web_auth.http_credential` |
| T | Cross-site WebSocket hijacking | Origin check on the handshake; socket closed at token expiry | `web_auth.websocket_credential`, `web_auth.hold_until_expiry` |
| R | "I did not change that camera" | `audit_events`: actor, source IP, time, details | `argus_common/audit.py`; call sites in both services |
| I | Cross-tenant reads | Every query filters by the caller's `tenant_id`; the audit API is tenant-scoped | routers; `tests/test_08_tenant_isolation.py`, `tests/test_audit_log.py` |
| I | Camera passwords via the API | URLs masked (`redact_url`) in every response; credentials restored server-side on edit | `argus_common/net.py`, `routers/cameras.py` |
| I | XSS reading tokens | Tokens are not script-readable; security headers | `deploy/nginx/snippets/security-headers.conf` (CSP still allows inline scripts: R3) |
| D | Request floods, expensive snapshots | Per-IP limits; snapshots 30/min per session; per-IP connection cap | `10-gateway.conf.template` |
| E | An operator performs admin actions | `require_admin` on every configuration route; the UI hides them | `app/services/auth.py` in both services |
| E | A spoofed client IP to dodge limits | uvicorn trusts `X-Forwarded-For` only from the edge (`FORWARDED_ALLOW_IPS`) | Dockerfiles, compose files |

### B2. Gateway → parking edge

| STRIDE | Threat | Control | Implemented in |
|---|---|---|---|
| S | Something else poses as parking, or as the gateway | mTLS with the private CA; gateway client certificate CN=gateway; parking certificate name verified | `deploy/pki.sh`, `05-gateway-to-parking.snippet.template`, `30-parking-internal.conf.template` |
| T, I | Traffic read or altered between hosts | TLS on 8443 | same |
| E | A browser reaches `/internal/*` through the gateway | The gateway returns 404 for `/internal/` | `10-gateway.conf.template` |

### B3. Service ↔ service (events and internal API)

| STRIDE | Threat | Control | Implemented in |
|---|---|---|---|
| S | A compromised parking host forges surveillance events (or the reverse) | Ed25519 service token bound to the mTLS CN, audience-bound, scoped per route, single use (`jti` replay cache); a peer may only publish event types it owns, as itself | `argus_common/tokens.py` (`ServiceTokenVerifier`, `ReplayCache`), `argus_common/mesh.py`, `app/routers/internal.py` |
| T | Malformed or oversized events | Pydantic contracts (`extra="forbid"`), 64 KiB cap | `argus_common/events.py`, `app/routers/internal.py` |
| R | Which peer caused this alert? | Handled events are audited as `service:<peer>` | `internal.event.handled` rows |
| D | Peer down: facts lost | Transactional outbox with retry and backoff; idempotent inbox | `argus_common/events.py` |
| E | A peer escalates beyond its scopes | Scopes per peer (`PEER_SCOPES`); the internal listener forwards only `/internal/*` | `app/mesh.py`, `20-surveillance-internal.conf.template`, `30-parking-internal.conf.template` |

### B4. Service → MediaMTX, and relay → MediaMTX

| STRIDE | Threat | Control | Implemented in |
|---|---|---|---|
| S | Anyone publishes a fake camera | Publishing requires the `mtx_publish` password; RTSP 8554 firewalled to relay addresses | `mediamtx.yml`, operator checklist in `SECURITY.md` |
| I | Anyone watches a camera | No anonymous read; each WebRTC session is authorised for that camera by the owning service through nginx `auth_request`; the viewer password is injected by the edge only | `/_auth/stream*` in the gateway, `web_auth.check_playback_request`, `routers/streams.py` (`playback_auth`) |
| T | Control API abused | API user `mtx_api` with a generated password; port on the internal network only | compose files, `scripts/generate-secrets.sh` |
| I | Cross-origin use of WHEP | `webrtcAllowOrigins` set to the public origin | `mediamtx.yml`, `MTX_WEBRTCALLOWORIGINS` |
| T, I | Relay traffic sniffed on the internet | RTSP over TCP with a password; **not encrypted** (R5) | — |

### B5. Service → database

| STRIDE | Threat | Control | Implemented in |
|---|---|---|---|
| I | A database dump exposes camera passwords | AES-256-GCM sealed columns with AAD and key ids; fail closed outside DEBUG | `argus_common/secretbox.py`, `app/services/camera_secrets.py` |
| I | A dump exposes refresh tokens or login subjects | Only SHA-256 of refresh tokens; HMACs of usernames and IPs | `sessions.py`, `login_attempts.py` |
| T | SQL injection | SQLAlchemy expressions only; audit filters validated | routers, `argus_common/audit.py` |
| R | Audit rows that do not match reality | The audit row is staged in the same transaction as the change | `add_audit_event` (stages, never commits) |
| I | A database reachable from the network | Ports never published; one DB per host on its Docker network | compose files |

### B6. Service → models and the LLM (ParkBot)

| STRIDE | Threat | Control | Implemented in |
|---|---|---|---|
| T, E | Prompt injection (plate text, activity log, chat) steers ParkBot into actions | Context delimited and sanitised; the output only *proposes* a whitelisted, validated action; the same admin must confirm it within 5 minutes; single use; tenant and ids never come from the model | `parking_assistant.py`, `parking_commands.py`, `ParkbotProposal` |
| T | Malicious model weights execute code on load | `torch.load` only with `weights_only=True` (torch ≥ 2.6 enforced); DeepSORT weights SHA-256 pinned; crime classifier built locally (no hub download) | `argus_vision/tracker.py`, `crime_classifier.py` |
| I | The LLM endpoint leaks in errors | Generic error message | `parking_assistant._ollama_chat` |

## 5. Residual risks

| # | Risk | Why accepted for now | Plan |
|---|---|---|---|
| R1 | Zones and grants are not in the database, so their audit row cannot share a transaction with the change (zones: a JSON file; grants: in memory). A database failure after the file write leaves an unaudited zone change. | The window is one failed commit. | Move zones into the database with the full baseline migration (correction 4.7). |
| R2 | The surveillance schema still comes from `create_all`; the Alembic baseline is stamp-only. | New migrations are guarded; the round-trip runs in CI. | Full baseline migration (4.7, `docs/REMAINING_WORK.md`). |
| R3 | The CSP allows `'unsafe-inline'` scripts (Next.js). | No known injection point; tokens are not script-readable. | Nonce CSP through Next.js middleware (4.4). |
| R4 | Application logs contain usernames and IPs. | Needed for operations; rotation of 30 days or less is documented. | Hash or drop them (4.9). |
| R5 | Relay → MediaMTX RTSP is unencrypted over the internet. | The relay sits on the camera LAN; publishing needs a password. | RTSPS, or WireGuard between relay and host. |
| R6 | Trivy is report-only; the Python images carry HIGH findings in setuptools' vendored packages (setuptools held at 80.9 for deep-sort's `pkg_resources`). | Build-time code, not reachable at runtime. | Replace deep-sort's embedder or vendor the tracker; then make Trivy blocking. |
| R7 | A compromised host holds its own keys (`/run/secrets`). | Inherent. | Separate hosts limit the blast radius; rotation procedures are in `SECURITY.md`. |
| R8 | Failed logins for unknown usernames are filed under the default tenant. | There is no account to scope them by. | Acceptable: admins of other tenants never see them. |

## 6. Phase 6 (face service): threats to design against

- **Biometric templates exfiltrated** → envelope encryption (AES-256-GCM, AAD = tenant + person id + key id), keys outside the database, fail closed.
- **The identify API used as a search engine** → internal mTLS only; every identify call audited with its purpose; rate-limited per caller.
- **Misidentification causing harm** → watchlist hits are "possible match — verify", never automatic action; thresholds per use.
- **Processing without consent or legal basis (DPDP)** → consent records per person; watchlist entries require a recorded legal basis; erasure endpoint; retention.
- **Poisoned or unsafe model files** → ONNX or `weights_only=True`, SHA-pinned weights, no pickle or joblib.
- **The sibling prototype's gaps** (no auth, fail-open crypto, `SHA-256(SECRET_KEY)` as the key, a `torch.load` fallback) are fixed in the port, not copied.

Detailed steps: `docs/REMAINING_WORK.md`.
