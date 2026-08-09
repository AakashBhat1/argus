<!--
=========================================================================
 HANDOVER CONTROL BLOCK  —  multi-AI-agent coordination
 This file is the single source of truth for who works on this repo next.
 Claude  = senior reviewer/architect/planner (writes this file, never code).
 codex   = MAIN backend implementer  ***CURRENTLY UNAVAILABLE***.
 grok    = covering codex's backend role this session + its own overflow/media.
 antigravity = frontend + validation.
 Implementers do the work and flip the switch back to claude.
=========================================================================
-->
---
current_session_worker: antigravity   # <-- THE SWITCH. Only this agent acts.
last_updated_by: grok
last_updated_at: 2026-07-13T15:10:00Z
codex_status: UNAVAILABLE             # grok covered all backend this session
agents:
  - claude        # senior dev: review, plan, design, pipeline, route. NEVER implements.
  - codex         # MAIN backend — UNAVAILABLE this session. Do not route to codex.
  - grok          # ACTING MAIN BACKEND (covering codex) + backend overflow + media.
  - antigravity   # frontend (UI/components/styling) + validation/QA passes.
protocol: |
  1. Each agent reads `current_session_worker` first.
  2. If it is not your name, STOP — do nothing, it is not your turn.
  3. If it is your name, do ONLY the task-board rows with `assigned_to: <you>`
     and `status: todo`, following each finding's acceptance criteria. Mark them
     done, then set `current_session_worker` to the next agent and update
     last_updated_by. Never touch rows assigned to another agent; never change
     the plan — only claude plans.
  4. After implementers finish a batch, hand control back to `claude` for the
     next review/route pass.
  5. SESSION NOTE: codex is UNAVAILABLE. All backend (codex's normal role) is
     assigned to grok. Phases run in order — grok completes the backend chain
     (BE-0 -> BE-6) before antigravity begins the frontend + validation chain.
routing:
  backend:            grok    # codex UNAVAILABLE -> grok is acting main backend
  video_photo_media:  grok
  frontend:           antigravity
  validation:         antigravity
  design_plan_route:  claude
---

# Handover: Smart Parking → Argus integration (blueprint v2 execution)

> Maintained by **claude** (senior reviewer). Source plan:
> `plans/parking-integration-blueprint-v2.md` (canonical vault copy:
> `C:\dev\second_brain\argus\architecture\parking-integration-blueprint-v2.md`).
> **codex is UNAVAILABLE this session — grok is the acting main backend.**
> Read the control block above before doing anything.

## Current status

- **Worker now:** `antigravity` (frontend + validation)
- **Last review:** 2026-07-13
- **Open items:** 2  |  **Done:** 7
- **Execution order:** BE-0…BE-6 **done** by grok. Next: antigravity runs
  FE-6 + VAL-7, then flips to `claude` for review.

### Non-negotiable invariants (from blueprint v2 — hold on EVERY backend task)
- **Tenant isolation:** every parking row is written with the ingesting
  camera's `tenant_id`; every read filters on `current_user.tenant_id`. No
  exceptions.
- **Auth:** no parking endpoint is anonymous. Reads → `get_current_active_user`.
  Writes/commands → `require_admin`.
- **Migrations, not `create_all`:** all schema changes ship as Alembic
  revisions. Prod is live PostgreSQL.
- **House style:** match existing `models.py` — surrogate `String(36)` UUID PKs
  via `generate_uuid`, `utc_now` defaults, `tenant_id` + composite indexes.

---

## Findings & task board

| ID | Lens | Title | Severity | Complexity | assigned_to | status | files |
|----|------|-------|----------|------------|-------------|--------|-------|
| BE-0 | api/pipeline | Establish Alembic + baseline revision (prod is live Postgres) | High | Med | grok | done | backend/yolo_classifier/alembic/*, alembic.ini |
| BE-1 | api/pipeline | Dependency & config alignment (RapidOCR, tariffs, OCR/Ollama settings) | Medium | Low | grok | done | requirements.txt, app/config.py |
| BE-2 | design | Tenant-scoped parking models + Camera.role/gate_roi + migration + per-tenant seeder | High | High | grok | done | app/models.py, alembic/versions/*, app/services/parking_seeder.py |
| BE-3 | design | Port OCR + parking business logic to async services (tenant-propagating) | High | High | grok | done | app/services/ocr_service.py, parking_service.py, parking_commands.py |
| BE-4 | api/pipeline | Smart OCR trigger in YOLO pipeline (gate ROI collision, once-per-track) | High | Med | grok | done | app/services/gate_ocr.py, intrusion_pipeline.py |
| BE-5 | security | Parking routers + ParkBot validated command layer (auth + tenant on every route) | Critical | High | grok | done | app/routers/parking.py, parking_chat.py, parking_assistant.py, main.py |
| BE-6 | validation | Backend unit tests (tariff calc, plate regex, command whitelist/validation) | High | Med | grok | done | tests/test_parking_*.py |
| FE-6 | design | Next.js 16 parking dashboard (grid + live split-view, retire SvelteKit) | Medium | High | antigravity | todo | frontend/src/app/(dashboard)/parking/* |
| VAL-7 | validation | Tenant-isolation + auth integration tests, e2e happy path, verify acceptance criteria | High | High | antigravity | todo | backend/.../tests/*, e2e/* (verify delivered work) |

`status` values: `todo` -> `in_progress` -> `done` (set by the assigned agent).
Routing this session: **all backend → grok** (codex unavailable);
**frontend + validation → antigravity**.

---

## Plan per finding (acceptance criteria for implementers)

### BE-0 — Establish Alembic migration scaffolding  →  grok  (Phase 0) ✅
**Problem:** No Alembic in `backend/yolo_classifier/` (confirmed — no
`alembic.ini`/`env.py`). Prod runs live PostgreSQL; `create_all` cannot alter
it safely. Every later schema change depends on this.
**Do:**
- Add Alembic to backend deps; run `alembic init` under
  `backend/yolo_classifier/` wired to the app's async engine / `DATABASE_URL`
  (autogenerate must import `app.models.Base.metadata`).
- Generate a **baseline** revision reflecting the *current* live schema (stamp,
  don't recreate existing tables). Confirm `alembic upgrade head` is a no-op
  against the current DB before any new tables are added.
- **De-nest check:** verify whether `parking-system/smart-parking-system/.git`
  exists (initial scan at depth ≤3 found none). If it exists, remove/convert it
  so it does not become a nested repo/submodule when merged. If it does not
  exist, note that in the Handoff log — do not fabricate the step.
**Done when:** `alembic upgrade head` / `downgrade` run cleanly against the
current schema; baseline revision committed; de-nest status recorded.

### BE-1 — Dependency & config alignment  →  grok  (Phase 1) ✅
**Problem:** RapidOCR + parking settings not yet in the Argus backend.
**Do:**
- Add `rapidocr-onnxruntime` (and any OCR runtime deps) to
  `backend/yolo_classifier/requirements.txt`, pinned.
- Port parking settings into `app/config.py` Pydantic settings: tariff table,
  OCR confidence threshold, Ollama model name/host. No hardcoded values in
  services — read from config.
- Add a short note (RAM impact: OCR loads permanently, ViT already ~1GB lazy)
  in the Handoff log to feed the instance-sizing decision. No secrets in source.
**Done when:** deps install; settings load from env/config; services can import
config values (no literals).

### BE-2 — Tenant-scoped models + migration + seeder  →  grok  (Phase 2) ✅
**Problem:** Parking needs 5 new tables + 2 Camera columns, all tenant-scoped.
**Do:**
- Add exactly the models from the blueprint (§Database Consolidation Plan):
  `VehicleProfile` (surrogate UUID PK, unique `(tenant_id, plate_text)`),
  `ParkingSpace`, `DetectedPlate`, `ParkingSession`, `ParkingActivityLog` —
  each with `tenant_id` + the specified composite indexes.
- Add `role` (`surveillance|gate_entry|gate_exit`) and `gate_roi` (JSON) to the
  existing `Camera` model.
- FKs point at **surrogate `id`**, never `plate_text` (avoids cross-tenant
  natural-key collisions).
- Generate ONE reviewed Alembic revision for all of the above (BE-0 must be
  done first).
- Add a **tenant-aware** seeder for parking spots (A-01 … F2-08) — never seed a
  global set; seed per tenant_id.
**Done when:** migration applies up/down cleanly on a scratch DB; models match
house style; seeder is tenant-scoped; no `create_all` used for these tables.

### BE-3 — Async OCR + parking business logic services  →  grok  (Phase 3) ✅
**Problem:** v1 logic is Flask/sync; Argus is async SQLAlchemy + tenant-scoped.
**Do:**
- Port `ocr_service.py` → `app/services/ocr_service.py` as a **RapidOCR
  singleton** (load once; expose `recognize_plate(crop) -> (plate, state, conf)`).
- Port `parking_service.py` → `app/services/parking_service.py` using **async
  SQLAlchemy**. Every function takes/propagates `tenant_id`; tariff calc,
  entry/park/release/checkout, activity logging all tenant-scoped.
- No blocking calls on the event loop (run OCR in a thread/executor if sync).
**Done when:** services are async, tenant-propagating, import config from BE-1,
and have no direct router/HTTP coupling (pure service layer).

### BE-4 — Smart OCR trigger in the YOLO pipeline  →  grok  (Phase 4) ✅
**Problem:** Replace continuous Haar/contour scan with tracker-driven, gated OCR.
**Do:**
- In `app/services/intrusion_pipeline.py`, add an ROI-collision hook: when a
  **tracked** `car`/`motorcycle` enters a camera's `gate_roi` and that camera's
  `role` is `gate_entry`/`gate_exit`, crop the vehicle **once per `track_id`**
  (de-dupe stationary vehicles) and call `ocr_service.recognize_plate`.
- Write a `DetectedPlate` row with the **camera's `tenant_id`** + `track_id` +
  `camera_id` provenance. Use `tracker.py` for track ids.
- Guard: OCR must never fire on non-gate cameras or for an already-seen track.
**Done when:** a car crossing a gate ROI produces exactly one tenant-scoped
`DetectedPlate`; non-gate feeds trigger no OCR; CPU cost is per-crossing, not
per-frame.

### BE-5 — Parking routers + ParkBot validated command layer  →  grok  (Phase 5) ✅
**Problem:** Every route must be authed + tenant-filtered; ParkBot must be a
validated propose→validate→execute path (v1's raw-text DB mutation is
prompt-injectable).
**Do:**
- `app/routers/parking.py`: implement the endpoints from the blueprint API
  table under `/api/v1/parking`. **All reads** →
  `Depends(get_current_active_user)` and `WHERE tenant_id == current_user.tenant_id`.
  **All mutations** (e.g. `/spaces/{id}/release`) → `Depends(require_admin)` with
  a tenant guard on the lookup (404 if the row is not in the caller's tenant).
  Mirror `routers/detections.py`.
- WS `/api/v1/parking/ws`: reuse the hardened **token-in-query** pattern; validate
  the token; broadcast only on the caller's **tenant channel**.
- `app/routers/parking_chat.py` + `app/services/parking_assistant.py`
  (async, singleton Ollama client): two personas —
  (1) **read** (any active user): answers via tenant-scoped async queries only,
  no write path;
  (2) **command** (`require_admin`): LLM returns a **structured action** JSON
  (e.g. `{"action":"release_space","space_id":"A-01"}`), then code
  (a) validates `action` against a whitelist,
  (b) validates args (plate regex `^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$`, `space_id`
  known within tenant),
  (c) executes the SAME service function the REST route uses,
  (d) writes a `ParkingActivityLog` row with `actor_user_id`.
  Never emit raw SQL/shell; unrecognized actions are rejected + logged.
- Register both routers in `app/main.py`.
**Done when:** no anonymous parking route exists; cross-tenant access returns
404/empty; non-admin mutation returns 403; ParkBot cannot mutate outside the
whitelist and logs every command with the actor.

### BE-6 — Backend unit tests  →  grok  (Phase 7, unit portion) ✅
**Problem:** Pure backend logic needs unit coverage before validation.
**Do:** Unit tests for tariff calculation, plate regex, and the ParkBot command
whitelist/arg validation. Fast, no live DB required (mock/session fixtures ok).
**Done when:** unit tests pass locally; cover the tariff/regex/whitelist logic
paths. (Integration + isolation + e2e are VAL-7, owned by antigravity.)

### FE-6 — Next.js 16 parking dashboard  →  antigravity  (Phase 6)
**Problem:** Retire SvelteKit app in `parking-system/smart-parking-system/web/`;
add parking panels to the existing Next.js **16** dashboard.
**Do:**
- Add `/parking` under `frontend/src/app/(dashboard)/`: interactive grid
  (floors G/1/2 × zones A/B/C). Cards: green=free, blue=VIP, red=visitor (entry
  time + live tariff), high-contrast flashing for blacklist. Click occupied →
  release modal (invoice, duration, "Process Checkout").
- Add `/parking/live`: split view — live YOLO stream + plate crops (left),
  ParkBot chat (right), scrolling entry/exit/security feed (bottom) driven by
  the tenant WS channel.
- Port the Three.js/Canvas visualizer to **React/Tailwind** to match the
  existing dashboard. All data through the tenant-scoped BE-5 endpoints; never
  call a parking endpoint without the auth token.
**Blocked by:** BE-5 (needs the live API + WS contract). Do not start until the
switch is `antigravity` and BE-0…BE-5 are `done`.
**Done when:** both routes render against the real tenant-scoped API; SvelteKit
app removed; visualizer is React/Tailwind; no Next.js-14-isms.

### VAL-7 — Tenant-isolation + auth integration/e2e + acceptance verification  →  antigravity  (Phase 7, validation)
**Problem:** The security invariants must be proven, not assumed.
**Do:**
- Integration tests: **tenant A cannot read or mutate tenant B's** spaces /
  plates / sessions (expect 404/empty, never leakage). Auth-required tests: 401
  without token on every route; 403 for non-admin on every mutation/command.
- E2E: entry → park → release → checkout happy path.
- Verify each BE finding against its "Done when" criteria; record pass/fail per
  item in the Handoff log.
- **Do NOT fix.** If a criterion fails, flip `current_session_worker: claude`
  and log it so claude can re-route to grok.
**Done when:** every delivered backend item is verified pass with isolation +
auth tests green and e2e happy path passing, OR failures are logged and routed
back to claude.

---

## API contract delivered (for FE-6)

Base: `/api/v1/parking` (Bearer JWT on REST; `?token=` on WS)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/stats` | active_user | tenant-scoped counts |
| GET | `/spaces` | active_user | auto-seeds 24 spaces per tenant if empty |
| POST | `/spaces/{id}/release` | **admin** | id = surrogate UUID or human `space_id` (e.g. `G-01`) |
| GET | `/plates` | active_user | recent detections |
| GET | `/plates/latest` | active_user | latest detection or null |
| GET | `/activity` | active_user | activity log |
| WS | `/ws?token=` | token validated | channel `parking`, tenant-isolated |
| POST | `/chat` | active_user (read); admin if `command:true` | ParkBot |
| POST | `/chat/command` | **admin** | validated propose→execute |

Space IDs seeded: `G-01`…`G-08`, `F1-01`…`F1-08`, `F2-01`…`F2-08` (zones A/B/C by slot).

---

## Handoff log

| When | From | To | Note |
|------|------|----|------|
| 2026-07-13 | claude | grok | Plan written from blueprint v2. codex UNAVAILABLE → grok is acting main backend. Backend chain BE-0…BE-6 routed to grok; FE-6 + VAL-7 to antigravity. Confirmed: no Alembic present (BE-0 must init it), no nested .git found at depth ≤3 (grok verify in BE-0), (dashboard) route-group + intrusion_pipeline.py + tracker.py + detections.py + auth.py all exist. |
| 2026-07-13 | grok | antigravity | BE-0…BE-6 complete (acting backend; codex unavailable). **De-nest:** `parking-system/smart-parking-system/.git` does **not** exist — no action needed. **RAM note:** RapidOCR loads permanently (~200–400MB with onnxruntime); ViT crime classifier remains ~1GB lazy — plan m7i-flex.large (or larger) accordingly. Alembic: baseline `20260713_0001` (stamp-only) + parking `20260713_0002`; `upgrade head` / `downgrade base` verified on scratch SQLite. Unit tests: `test_parking_*.py` all green. FE should use API contract section above. Next: FE-6 + VAL-7. |

<!--
 IMPLEMENTERS: after finishing your assigned `todo` items, set each to `done`,
 add a row to the Handoff log, and set `current_session_worker` to the next
 agent (grok backend chain -> antigravity for FE-6/VAL-7 -> claude for the next
 review pass). Only claude edits the plan.
-->
