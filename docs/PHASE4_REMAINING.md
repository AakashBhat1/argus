# Phase 4 (security hardening): what is left

Status as of 2026-09-24, branch `claude/eloquent-hawking-13xed5`.

## Already done in Phase 4 (for context)

| Commit | What |
|---|---|
| `123d6b7` | httpOnly `__Host-` cookie sessions, rotating refresh tokens with reuse detection, CSRF + Origin checks, WebSocket Origin check, sockets close at token expiry |
| `f84b979` | Shared WebSocket hub: concurrent, time-bounded sends; slow clients dropped; per-tenant connection cap |
| `f140ded` | Per-camera WebRTC playback authorisation (nginx `auth_request`); anonymous MediaMTX read removed; MediaMTX pinned to 1.21.1 (1.9.3 ignored the password env overrides) |
| `42006e9` | Camera credentials sealed at rest (AES-256-GCM), masked in the API; admin-only camera changes |
| `96a1abb` | Spoof-proof client IPs (edge-only `FORWARDED_ALLOW_IPS`), DB-backed login limiter, first-admin bootstrap token / `create_admin` CLI, password policy, private health endpoint |
| this commit | pyflakes clean-up (0 findings); crime classifier builds its ViT architecture locally instead of downloading `google/vit-base-patch16-224` at runtime (config verified equal to `ViTConfig` defaults) |

Test counts at this commit: argus_common 87, surveillance 211, parking 104, frontend 9, all passing. `deploy/check.sh` passes.

---

## 1. CI security checks

These were never wired into CI. Each item below goes in `.github/workflows/`.

### 1.1 Python lint gate
- Add `ruff check --select F packages services scripts` to CI. The code is clean as of this commit, so the gate starts green.
- Later, widen it to `E,W,B,S` (bugbear and bandit rules). Fix or `# noqa` each finding with a reason.

### 1.2 Bandit
- Add `bandit -q -ll -ii -r packages/argus_common/argus_common packages/argus_vision/argus_vision services/surveillance/app services/parking/app`.
- It reports no medium/high findings now. The only one, B615 (the unpinned Hugging Face download in `crime_classifier.py`), was fixed in this commit.
- Low-severity findings (`try/except/pass`, asserts, header names flagged as "passwords") need triage, not a CI gate.

### 1.3 pip-audit (dependency CVEs)
- In the per-service CI job, after installing, run `pip install pip-audit && pip-audit --skip-editable`.
- Also run it for the `packages/argus_common` job.
- Expect findings on old pins. Candidates:
  - `uvicorn==0.27.1` (current releases are 0.3x)
  - `fastapi`, `starlette`, `python-multipart`, `passlib`, `torch`
- For each finding, either bump the pin (then rerun all suites and the Alembic round-trip), or add `--ignore-vuln <ID>` with a comment explaining why the code path is not reachable.
- passlib is unmaintained. Plan a move to `bcrypt` directly or to `argon2-cffi`, keeping verification of existing bcrypt hashes. Rehash on the next successful login.

### 1.4 npm audit
- In the frontend job, run `npm audit --omit=dev --audit-level=high`.
- Fix with `npm update` / `npm audit fix` without `--force`. Rerun lint, typecheck, tests and build.

### 1.5 Secret scanning (gitleaks)
- New job using `gitleaks/gitleaks-action@v2` or the pinned binary, with `fetch-depth: 0` so the full history is scanned.
- Add `.gitleaks.toml` with an allowlist only for the known public placeholders in history:
  - `mtx_api_change_me`
  - `mtx_publish_change_me`
  - `replaced-by-env`
  - test-only keys under `tests/`
- Any real secret found in history must be rotated. Removing it from history alone is not enough.

### 1.6 CodeQL
- `.github/workflows/codeql.yml` with languages `python` and `javascript-typescript`.
- Triggers: push, pull request, and a weekly cron. Use `security-and-quality` queries.

### 1.7 Dependabot
- `.github/dependabot.yml` with weekly updates, grouped minor/patch, for these ecosystems:
  - `pip`: `/services/surveillance`, `/services/parking`, `/packages/argus_common`, `/packages/argus_vision`
  - `npm`: `/frontend`
  - `github-actions`: `/`
  - `docker`: `/services/surveillance`, `/services/parking`, `/frontend`

### 1.8 Container image build (never verified)
- The images could not be built in the development sandbox, which blocks `deb.debian.org`. No one has built them yet.
- Add a CI job that runs:
  - `docker build -f services/surveillance/Dockerfile .`
  - `docker build -f services/parking/Dockerfile .`
  - `docker build frontend`

  Use no push, and the `buildx` cache.
- Then scan each image with Trivy (`aquasecurity/trivy-action`, `severity: CRITICAL,HIGH`, `ignore-unfixed: true`). Start report-only and make it blocking once the baseline is clean.
- Smoke test: run each image with `--read-only --cap-drop ALL --security-opt no-new-privileges`, `DEBUG=true` and the stub model (`scripts/dev/make_stub_detector.py`). Curl `/api/v1/health` and `/api/v1/parking/health`.
- Pin base images by digest (`python:3.12-slim-bookworm@sha256:…`, `node:22-alpine@sha256:…`, `nginx:1.27-alpine@sha256:…`, `postgres:15-alpine@sha256:…`, `bluenviron/mediamtx:1.21.1@sha256:…`). Let Dependabot bump them.

**Done when:** all the jobs above are green on the branch, and a vulnerable pin makes CI red.

---

## 2. Admin audit log

There is no record of who changed what. Login failures are only logged to stdout.

### 2.1 Surveillance
- **Model:** `AuditEvent` (table `audit_events`) with these columns:
  - `id`, `created_at`, `tenant_id`, `actor` (username, or `system` / `service:<name>`), `actor_id`
  - `action` (dotted, e.g. `camera.update`), `target_type`, `target_id`
  - `details` (JSON, **never secrets**: use `redact_url` for stream URLs, no passwords or tokens)
  - `source_ip`
- Alembic migration `20260924_0010`.
- `app/services/audit.py`: `record(db, request, actor, action, target_type, target_id, details)`. It writes in the **same transaction** as the change it describes, so a rolled-back change leaves no audit row and vice versa.
- **Call sites:**
  - user create, including HTTP bootstrap and `app/cli/create_admin.py`
  - login success, failure and lockout (`auth.login.*`; username as typed, truncated)
  - session refresh-token reuse (`auth.session.reuse_revoked`) and logout
  - camera create, update and delete (`routers/cameras.py`)
  - zone create, update and delete (`routers/zones.py`)
  - arm-mode change, and grant create and revoke (`routers/security.py`)
  - vehicle registration
  - inbound peer events handled (`routers/internal.py`, actor `service:parking`)
- **API:** `GET /api/v1/audit`, admin only and tenant-scoped. Filters `action`, `actor`, `since`, `until`; paginated (`limit` ≤ 500, cursor on `created_at,id`).
- **Retention:** `AUDIT_RETENTION_DAYS` (default 365), purged by `app/services/retention.py`.

### 2.2 Parking
- The same table and migration (`services/parking/alembic/versions/20260924_0003_audit_events.py`) and the same helper.
- **Call sites:**
  - camera CRUD
  - vehicle-profile CRUD
  - every **parking assistant (ParkBot) action** (`app/services/parking_assistant.py` / `parking_commands.py`), recording the raw user message, the extracted action, the validation result and the executed result
  - arm-mode events received
- **API:** `GET /api/v1/parking/audit`, admin only.

### 2.3 Frontend
- An "Audit log" page for admins, with a table, filters and a date range. Hide it for operators using the role in the session hint (`src/lib/auth.ts`).

### 2.4 Tests
- Every call site writes exactly one row.
- A rolled-back change writes none.
- Details never contain `S3cret`-style credentials.
- Operators get 403 on `/audit`.
- Tenant A cannot see tenant B's events.
- Retention purges old rows.

---

## 3. Documentation

### 3.1 `SECURITY.md` (currently 20 lines)
Keep the reporting section. Add:
- A security model summary (three services, edges, mTLS, service tokens, cookie sessions), linking the threat model.
- **Keys to back up and how to rotate them:**
  - `surveillance/auth.key`: move the old public key into `AUTH_PREVIOUS_PUBLIC_KEYS_DIR`, restart, and wait out the access-token lifetime.
  - `<svc>/service.key`: run `deploy/pki.sh init`, then redistribute the peers' `.pub.pem` files.
  - `<svc>/camera-secrets.key`: move the old key into `CAMERA_SECRETS_PREVIOUS_KEYS_DIR`, then restart; the service re-seals at startup. **Losing this key loses every stored camera URL.**
  - TLS: `deploy/pki.sh renew <svc>`.
  - MediaMTX passwords: `.env`, then `scripts/generate-secrets.sh` (it re-derives `MEDIAMTX_VIEWER_BASIC`), then recreate `mediamtx` and `edge`.
- An operator checklist:
  - `DEBUG=false`
  - no `AUTH_BOOTSTRAP_TOKEN` left set after first use
  - firewall only 80/443, RTSP 8554 from the relay IPs, 8189/udp, and 8443 between hosts
  - backups of the databases and `deploy/pki/out`

### 3.2 Threat model
- Update `plans/argus-threat-model.md`, or move it to `docs/threat-model.md`, for the three-service topology:
  - assets: video, camera credentials, identities, plates, future face templates
  - actors
  - trust boundaries: browser→gateway, gateway→parking edge, service↔service, service→MediaMTX, relay→MediaMTX, service→DB
- For each boundary, give STRIDE threats → the implemented control → the file that implements it.
- List residual risks (section 5).

### 3.3 `docs/upgrading.md`
From the pre-split single-container stack, in order:
1. Back up the databases.
2. Run `git pull`.
3. Run `deploy/pki.sh init`. It creates camera-secrets keys even for existing PKIs.
4. Run `scripts/generate-secrets.sh`. This adds `PARKING_POSTGRES_PASSWORD`, `MEDIAMTX_READ_PASSWORD`, `MEDIAMTX_VIEWER_PASSWORD` and `MEDIAMTX_VIEWER_BASIC`.
5. `docker compose up -d surveillance-db parking-db`
6. Parking: `alembic upgrade head`
7. `migrate_parking_data.py --source … --target … [--source-camera-key …] [--target-camera-key …]`
8. Surveillance: `ARGUS_PARKING_DATA_MIGRATED=1 alembic upgrade head`
9. `docker compose up -d`
10. If there are no users yet: `docker compose exec surveillance python -m app.cli.create_admin --username admin`

Also cover:
- Everyone is signed out once, because localStorage tokens are replaced by cookie sessions.
- **Existing live host advisory:** the old stack used `bluenviron/mediamtx:latest` with the placeholders `mtx_api_change_me` / `mtx_publish_change_me` and anonymous read. On the running host, check `curl -u mtx_api:mtx_api_change_me http://127.0.0.1:9997/v3/paths/list`. A 200 means anyone who read the public repo could publish to or control the media server. Rotate the credentials and upgrade.

---

## 4. Smaller Phase 4 corrections still open

| # | Where | Problem | Fix |
|---|---|---|---|
| 4.1 | `deep_sort_realtime` embedder (third-party) | `torch.load(..., weights_only=False)` on its bundled weights; the warning appears in the test run | Verify the bundled weight file's SHA-256 at startup, or use an embedder loaded with `weights_only=True` |
| 4.2 | `services/parking/app/services/parking_assistant.py` | The admin ParkBot executes structured actions extracted from LLM output (prompt injection could steer it) | Keep `ALLOWED_ACTIONS` minimal; require explicit user confirmation for state-changing actions; audit every action (2.2); never let the LLM set tenant or ids outside the caller's tenant |
| 4.3 | Frontend | Admin-only actions (camera create/edit/delete, zones, users) still show for operators and fail with 403 | Role-aware UI from the session hint |
| 4.4 | `deploy/nginx/snippets/security-headers.conf` | CSP allows `'unsafe-inline'` scripts and styles (Next.js) | Nonce-based CSP via Next.js middleware, then drop `'unsafe-inline'` for scripts |
| 4.5 | Compose files | No memory, CPU or pids limits | Add `pids_limit` and `mem_limit` sized per host; document the sizing |
| 4.6 | `mediamtx.yml` | `webrtcAllowOrigin` defaults to `*` (cookies are not sent cross-site, so this is not exploitable, but it should be tight) | Set it to the public origin via env |
| 4.7 | Surveillance DB | The Alembic baseline is stamp-only; tables come from `init_db()` `create_all` | Make migrations the only schema source (a full baseline migration) |
| 4.8 | `services/surveillance/app/services/websocket_manager.py` usage | Per-tenant cap only, no per-user cap | Add a per-user cap (e.g. 20) to `WebSocketHub.connect` |
| 4.9 | Logs | Usernames and IPs appear in plaintext logs (login warnings, access logs) | Decide log retention and whether to hash them, for DPDP |
| 4.10 | `/api/v1/streams/{id}/snapshot`, `/parking/cameras/{id}/snapshot` | Expensive and unthrottled | Rate-limit per user in the edge (`limit_req` on those paths) |
| 4.11 | `docker-compose.yml` backplane | Postgres traffic inside the backplane is unencrypted | Acceptable on one host. For split hosts the DBs stay local; document that DB ports are never published |

---

## 5. How to verify the end of Phase 4
1. `deploy/check.sh` passes.
2. All suites pass:
   - argus_common, parking, surveillance: `pytest -m "not requires_model"`
   - frontend: `npm run lint -- --max-warnings=0 && npm run typecheck && npm test && npm run build`
3. Alembic `upgrade head` / `downgrade base` round-trip for both services.
4. Every CI job is green, including the new security, CodeQL and image jobs, on the pushed branch.
5. Browser end-to-end (as run for `123d6b7`):
   - sign in, refresh, WebSocket reconnect, cross-origin attacker page blocked, logout
   - camera playback allowed for the owner and refused for others
   - audit rows visible to an admin, 403 for an operator
