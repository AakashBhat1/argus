# Argus: remaining work (Phases 5–7)

Status: 2026-11-08. Written after Phase 4 (PR #4). This is a plan: none of it is implemented.

**There is no live host any more** (the EC2 instance is shut down). Everything below is built and verified on the local machine; the deployment steps describe what a future host would need.
It was written from the code, and every existing file, function and setting
named here was checked against it. New names are proposals.

Each step lists: files, functions and data models, API shapes, maths (where
there is any), tests, acceptance criteria, risks.

Work rules from the earlier phases still apply: a branch off `main` per phase,
small commits, the verification commands near the end of this document before every push, a PR at the
end of each phase, never push to `main`. Do Phase 5 first. Phase 6 depends only
on Phase 4. Phase 7 is last.

## 0. Findings that change the original plan

| # | Finding | Consequence |
|---|---|---|
| F1 | `distance_estimator` at HEAD (7717b54) is no longer the MiDaS Python prototype. It is a Go + ONNX app using Depth Anything V2 Small, metric, **trained for indoor scenes with a 20 m ceiling**. The MiDaS code survives at commit 18d12ac. | Phase 5 ports the *ideas* from 18d12ac (bbox median, percentile trim). MiDaS → OpenVINO stays the default for outdoor gates, and Depth Anything becomes a per-camera switch for closed areas (decision D1). |
| F2 | The prototype's calibration is wrong for MiDaS. It uses `distance = scale / measured` with one scale. MiDaS predicts inverse depth only up to an unknown **scale and shift**, per image. | Step 5.5 fits `1/z = a·m + b` on each MiDaS frame from ground-plane references. No manual depth calibration is needed. |
| F3 | Both sibling repos already have no committed `__pycache__` or `egg-info` (`git ls-files` finds none). | Phase 7's sibling cleanup shrinks to a README pointer plus a `.gitignore` check. |
| F4 | `publish_theft_suspected` (parking `app/services/events.py`) is defined and its handler exists in surveillance, but **nothing calls it**. | The theft check is new work (step 6.12), not wiring. |
| F5 | `gate_exit` currently only sets the event direction. It does not close a session; sessions close by manual release, ParkBot or `vision_checkout` from occupancy. | Step 6.11 must handle exit explicitly. |
| F6 | `IdentityProvider.identify()` is **synchronous** and runs inside `AuthorizationRegistry.resolve()`, inside the frame loop. The face service is remote. | Step 6.10 uses a local cache fed asynchronously. Never call the network from `identify()`. |
| F7 | Parking has a `calibration` column but its pipeline does no geometry, and there is no risk engine there. `CameraGeometry.locate` is only called from surveillance's `RiskEngine.evaluate`. | Step 5.7 adds it to parking separately. |
| F8 | In the facial-recognition prototype, `recognize_multi_frame` takes the best score per frame **across all users**, then aggregates. Five frames can match five different people and still be "recognized". | Fixed in the port: aggregate per identity (step 6.6). |
| F9 | Zones were not tenant-scoped. | Fixed in PR #4: `roi_zones` table, tenant-filtered API and runtime. |
| F10 | The repo's `LICENSE` file is effectively empty (5 bytes of whitespace), and there is no `NOTICE`. | Step 7.4. Decide the licence before distributing anything: YOLO weights are AGPL-3.0. |

## 0.1 Owner decisions (2026-11-08)

Argus is an early-stage development project and is not being sold.

| # | Decision | Effect on the plan |
|---|---|---|
| D1 | MiDaS is the default depth model. A camera in a closed area can switch to Depth Anything V2. | Per-camera `depth_backend` (5.5), a dropdown (5.6), and a comparison tool, so the choice per camera rests on measurements. |
| D2 | Watchlist matching is **on** by default and can be switched off. | `FACE_WATCHLIST_ENABLED=true`, with a global off switch (6.9). "Possible match — verify, never automatic action" still holds. |
| D3 | Keep face at the gate; if it cannot run, a guard decides. FASTag may be added later, and the face step must be replaceable. | A pluggable `GateSecondFactor` (6.11). Private buildings use face; a FASTag reader can be added for public lots without touching the decision logic. |
| D4 | Non-commercial for now. | InsightFace weights are acceptable. `FACE_ACK_NONCOMMERCIAL_WEIGHTS=true` goes in the development settings; the check stays so it cannot be forgotten if the project is ever sold. |

**Still open.** Q5: the licence for Argus (`LICENSE` is blank and the repository is public). Nothing is blocked until it is distributed.

**Limit on D2.** Not selling the project does not by itself take it outside the DPDP Act; the Act exempts purely personal or domestic use. During development, enroll only people who have agreed to it (`consenting_test_subject`, 6.3), and take legal advice before pointing a watchlist at the general public.

---

## Phase 5: distance to the gate

**Goal.** For each tracked person and vehicle: `gate_distance_m`, approach speed (EMA) and ETA from ground-plane geometry. MiDaS is used only when the foot point is hidden. New risk signals. Dashboard overlay and calibration UI.

**What exists.** `CameraGeometry` has a homography, recovered camera pose, `foot_to_ground`, `range_from_camera` and `locate` (`packages/argus_vision/argus_vision/geometry.py`). `CameraCalibration` holds `hfov_deg`, four image and world points, `camera_height_m`. `RiskEngine._TrackContext.ground_points` is a 5-second deque of `(t, x_m, y_m)`. `ZoneEditor.tsx` has a calibration tab. Note that `TrajectoryFeatures` speeds are in pixels; use ground points only.

### 5.1 Gate line in the calibration

**Files.** `packages/argus_vision/argus_vision/geometry.py`; `packages/argus_vision/argus_vision/schemas.py` (`CameraCalibrationSchema`, shared by both services).

**Data model.** No migration: `Camera.calibration` is already JSON in both services. Extend `CameraCalibration`:

```python
gate_line_image_points: list[list[float]] = field(default_factory=list)  # 2 normalized [u, v]
gate_inside_sign: int = 1          # +1 or -1: which side of A→B is "inside"
gate_zone_id: Optional[int] = None # the entrance zone whose schedule arms this gate
depth_backend: str = "off"         # "off" | "midas_small" | "depth_anything_indoor" (5.5)
```

`from_dict` validates: exactly 0 or 2 points, each in [0, 1], `gate_inside_sign` in {-1, 1}.

**Functions.** `CameraGeometry.gate_segment() -> Optional[tuple[GroundPoint, GroundPoint]]`: maps both ends through `foot_to_ground` after denormalizing. Returns `None` without a homography, or when the mapped length is below 0.5 m or above 100 m (a line drawn near the horizon).

**Tests.** Identity-like homography maps the line exactly; degenerate lines give `None`; round-trip `to_dict`/`from_dict`; invalid payloads are refused (422 through the API).

**Acceptance.** Saving a gate line on a calibrated camera persists it, and reloading the page shows it.

**Risks.** The homography is only trustworthy near its four calibration points. Warn in the UI when a line end lies outside their convex hull (extrapolation).

### 5.2 Gate kinematics (the core maths)

**Files.** New `packages/argus_vision/argus_vision/gate_kinematics.py`.

**Models.**

```python
@dataclass(frozen=True)
class GateKinematics:
    distance_m: Optional[float]
    distance_sigma_m: Optional[float]
    signed_side: int                  # +1 inside, -1 outside, 0 unknown
    approach_mps: Optional[float]     # positive = closing on the gate
    eta_s: Optional[float]
    source: Optional[str]             # "ground_plane" | "depth_estimate"

@dataclass(frozen=True)
class GateTrackState:             # one per track; replaced on each update
    last_t: float
    filtered_d: float
    filtered_v: Optional[float]
    samples: int

def update_gate_state(state, *, t, point, segment, inside_sign, class_label, source,
                      config) -> tuple[Optional[GateTrackState], GateKinematics]: ...
```

**Maths.** With gate segment A→B and ground point P:

- Closest point: `s = clamp(((P−A)·(B−A)) / |B−A|², 0, 1)`, `Q = A + s(B−A)`, distance `d = |P−Q|`.
- Side: `signed_side = gate_inside_sign · sign((B−A) × (P−A))`.
- Time-aware EMA, so that irregular frame rates do not change the filter: `α(Δt) = 1 − exp(−Δt/τ)`.
  - Distance: `d̄ₖ = d̄ₖ₋₁ + α(Δt; τ_d)(dₖ − d̄ₖ₋₁)`, `τ_d` = 0.5 s.
  - Speed: `vₖ = −(d̄ₖ − d̄ₖ₋₁)/Δt`, `v̄ₖ = v̄ₖ₋₁ + α(Δt; τ_v)(vₖ − v̄ₖ₋₁)`, `τ_v` = 1.5 s. Positive means approaching.
- ETA: `eta = d̄/v̄` when `v̄ ≥ 0.3 m/s` and `eta ≤ 120 s`, else `None`.
- Guards: Δt < 0.05 s merges the sample (no update); Δt > 2 s resets the filter; a jump implying more than 12 m/s (persons) or 45 m/s (vehicles) is dropped as a tracker or homography glitch. `approach_mps` stays `None` until 3 samples.
- Uncertainty, by propagating pixel error through the homography. With `H` and `w = h₃·(u,v,1)`:

```
∂x/∂u = (h11 − x·h31)/w    ∂x/∂v = (h12 − x·h32)/w
∂y/∂u = (h21 − y·h31)/w    ∂y/∂v = (h22 − y·h32)/w
n̂ = (P−Q)/d        σ_d = σ_px · ‖Jᵀn̂‖
```

  with `σ_px = 0.02 · bbox_h` (foot-point jitter, an assumption to tune on site). Expose `distance_sigma_m`; it grows with range, which is the honest behaviour.

**Tests** (synthetic, no model). Constant 1.5 m/s approach from 20 m gives `approach_mps` within 5 % after 5 s and an ETA within 10 %; Gaussian foot noise of 0.1 m keeps speed within ±0.3 m/s; walking parallel to the gate gives `|v| < 0.3` and no ETA; a track beyond the segment end uses the endpoint; irregular Δt gives the same result as regular Δt (±5 %); a teleport sample is dropped; a 3 s gap resets; side changes when crossing the line; σ grows with range.

**Acceptance.** Module coverage at or above 90 %. All functions pure; state replaced, never mutated.

**Risks.** Tracker id switches restart the filter, costing about 1 s of warm-up. Acceptable.

### 5.3 Wire kinematics into the surveillance pipeline

**Files.** `services/surveillance/app/services/risk_engine.py`.

**Changes.** In `RiskEngine.evaluate`, after `locate()`, for every object (persons *and* vehicles) with a `ground_x_m`, update a per-track `GateTrackState` held in `self._gate_states: dict[int, GateTrackState]`, expired alongside `_expire`. Write onto the object dict: `gate_distance_m`, `gate_distance_sigma_m`, `gate_side`, `gate_approach_mps`, `gate_eta_s`, `distance_source`. Add `gate_distance_m` and `gate_eta_s` to `RiskEvent` and `to_dict`.

Note that `_contexts` only covers persons today, so vehicles need this separate state map.

**Tests.** A camera with a gate line and a walking synthetic track yields the fields in the broadcast payload; a camera with no gate line yields `None` fields and no error; state is released when a track ends.

**Acceptance.** Per-frame added latency under 1 ms with 50 tracks. No regression in the suite.

### 5.4 Risk signals

**Files.** `risk_engine.py` (`_score`, `_TrackContext`), `services/surveillance/app/config.py`.

**Settings.** `RISK_GATE_FAST_MPS=2.5`, `RISK_GATE_SPRINT_MPS=6.0`, `RISK_GATE_FAST_MAX_M=40`, `RISK_GATE_FAST_MAX_ETA_S=10`, `RISK_GATE_FAST_HOLD_S=1.0`, `RISK_GATE_LOITER_M=8`, `RISK_GATE_LOITER_S=30`, `RISK_GATE_STILL_MPS=0.3`.

**Rules** (persons on the outside of the gate, `source == "ground_plane"` only):

- **Armed gate:** `arm_mode == "armed"`, or `arm_mode == "auto"` and the linked `gate_zone_id` passes `_zone_armed`. With no linked zone, only `armed` counts.
- **Fast approach:** armed, `v̄ ≥ FAST`, `d̄ ≤ FAST_MAX_M`, `eta ≤ MAX_ETA`, sustained for `HOLD_S` (`ctx.gate_fast_since`):
  `score += 20 + 15·clamp((v̄ − FAST)/(SPRINT − FAST), 0, 1)`, reason `approaching gate at {v:.1f} m/s (ETA {eta:.0f}s)`.
- **Loitering near the gate:** `d̄ ≤ LOITER_M` and `|v̄| < STILL` for at least `LOITER_S` (`ctx.gate_loiter_since`):
  `score += 15 + min(10, 10·(dwell − LOITER_S)/60)`, reason `loitering {dwell:.0f}s near the gate`.

With the thresholds of 25, 50 and 75, either signal alone reaches at most "suspicious". An alert needs a second factor (quiet hours, perimeter origin). That is deliberate: a jogger must not page anyone. The existing `score *= 0.2` for authorized persons still applies.

**Tests.** Each rule fires and stays silent at its boundary; disarmed does nothing; depth-only tracks never score; an authorized person is scaled; `hold` prevents a one-frame spike firing; loiter resets when the person leaves.

**Acceptance.** Scenario tests: sprinter at night while armed reaches "alert"; pedestrian passing parallel stays "observe".

**Risks.** Thresholds are guesses until tuned. Log the features for two weeks before relying on the alerts.

### 5.5 Offline MiDaS (off by default, only when the foot is hidden)

**Files.** New `scripts/dev/convert_midas_openvino.py`, `packages/argus_vision/argus_vision/depth.py`, `packages/argus_vision/argus_vision/models.lock.json`. Settings go in `argus_vision/settings.py`: `DEPTH_ENABLED=false` (master switch), `DEPTH_MODELS_DIR="models/depth"`, `DEPTH_MAX_FPS=2.0`. New `scripts/dev/compare_depth.py`.

**Getting the model.** Use the published `model-small.onnx` (66,764,249 bytes) from the `isl-org/MiDaS` `v2_1` release; MIT licence. This avoids torch and `torch.hub` entirely. The repo's `torch.hub.load(..., trust_repo=True)` path also nests a second hub download (gen-efficientnet), which is exactly what must not run.

The conversion script runs once, on a developer machine: downloads the file, compares it to a SHA-256 constant (GitHub publishes no digest for this release, so compute it on first download, record it in the script in review, and verify from a second network), runs `ov.convert_model`, `ov.save_model`, and writes `models.lock.json` with SHA-256 of the source ONNX, the `.xml` and the `.bin`.

**Per-camera switch (D1).** Each camera chooses through `CameraCalibration.depth_backend`, and depth runs only when `DEPTH_ENABLED` is also true:

```python
class DepthBackend(Protocol):
    name: str
    kind: Literal["relative_inverse", "metric"]
    def infer(self, frame: np.ndarray) -> DepthMap: ...   # values plus the image region they cover

BACKENDS = {"midas_small": MidasSmall, "depth_anything_indoor": DepthAnythingIndoor}
```

| | `midas_small` (default) | `depth_anything_indoor` (closed areas) |
|---|---|---|
| Output | Relative inverse depth; needs the per-frame fit below | Axial depth in metres; no fit needed |
| Works | Indoors and outdoors, given a calibrated ground plane | **Indoor scenes only**, up to 20 m |
| Needs a ground plane | Yes, for the fit | For the foot position only |
| Input | 256×256 | 392×392 **centre crop**, no stretching |
| Licence | MIT | Apache-2.0 (Small model) |
| Source | `isl-org/MiDaS` v2_1 `model-small.onnx` | `kornia/depth-anything`, revision `86f40563e7e87a0839be758988aa804930409a61`, `depth-anything-v2-metric-small-indoor.onnx`, SHA-256 `50dbcac7a6d667e365a3ceffdf51cc497aa5e06b6d2c1d5824c252640fbf5bf3` (the pin `distance_estimator` already uses). A third-party conversion, not published by the model's authors. |

Depth Anything specifics, carried over from `distance_estimator`: a person outside the centre crop gets `None`; readings at 19.5 m or more are rejected as saturated; sample the central 60 % of the box. Its `z` goes straight into the range formula below. One compiled model per backend, loaded on first use.

**Loading.** Each backend verifies its hashes **before** `ov.Core().compile_model`, and raises `UnsafeModelError` on mismatch (the same pattern as `verify_embedder_weights`). No network, torch, or pickle.

**Choosing per camera.** Whether Depth Anything really is better in a given room has to be measured. `compare_depth.py --clip room.mp4 --refs refs.json` runs both backends on the same tape-measured clip and prints median error and how many frames gave a usable reading.

**Runtime.** `infer(frame) -> np.ndarray` runs through `asyncio.to_thread`, with one compiled model per process, drop-when-busy, at most `DEPTH_MAX_FPS` per camera.

**Trigger.** `foot_visible(bbox, vehicles, frame_h) -> bool`: false when the box touches the bottom edge, or a vehicle box overlaps the bottom 20 % of a person's box. Depth is only requested for persons within tracking whose foot is hidden.

**Maths: per-frame alignment (MiDaS only).** MiDaS gives `m`, inverse depth up to scale and shift, per frame. Fit it from the ground plane in the same frame. Sample a 24×24 pixel grid inside the convex hull of the calibration points, excluding all detection boxes. For each sample pixel:

```
x = (u−cx)/fx,  y = (v−cy)/fy
r = ground range from the camera foot (via foot_to_ground and the pose)
ρ = sqrt(r² + h_c²)             # h_c = pose.height_m
z = ρ / sqrt(1 + x² + y²)       # pinhole: independent of camera orientation
```

Solve `1/z ≈ a·m + b` by Huber-weighted least squares (IRLS, 5 iterations). Accept only if there are at least 12 samples, `z_max/z_min ≥ 1.5`, `a > 0`, and the median of `|ẑ−z|/z` is at most 0.20. Otherwise return `None` for this frame.

For a person: `m_p` is the median of the central 60 % of the box width and upper 70 % of its height, after a 5–95 percentile trim. Then (Depth Anything starts from its own `ẑ`):

```
ẑ = 1/(a·m_p + b)                  (MiDaS; require the denominator > 0)
ρ = ẑ·sqrt(1 + x_c² + y_c²)
r = sqrt(max(ρ² − (h_c − h_t)², 0))   h_t = 1.1 m (torso centre)
G = foot_to_ground(u_c, v_bottom)  (the visible bottom of the box)
P = C + r·(G − C)/|G − C|          C = camera ground position
```

Requires `pose.height_m`; without it, depth stays off for that camera and the UI says so.

Depth samples are shown as `distance_source: "depth_estimate"` (plus `depth_backend`) with `distance_sigma_m = max(1.0, 0.15·r)`. They update `gate_distance_m` only: they do **not** update the speed filter, and never drive risk scoring.

**Tests.** Hash mismatch refuses to load; a missing file with `DEPTH_ENABLED=false` is fine, and a camera selecting that backend with `true` fails clearly; the switch selects per camera and `off` runs no depth code; Depth Anything rejects saturated readings and boxes outside the crop; with synthetic `m = (1/z − b)/a` plus 3 % noise, `(a, b)` are recovered within 2 %; a shift term that the old single-scale method gets wrong by over 20 % is handled; degenerate scenes (flat range, `a ≤ 0`) give `None`; the stub OpenVINO model (extend `scripts/dev/make_stub_detector.py`) proves the plumbing with no weights. Real-model test marked `requires_model`.

**Acceptance.** `grep -r "torch.hub\|trust_repo" packages services` finds nothing. With the flag off, no depth code runs. On a site clip with hidden feet, median absolute relative error is at most 25 % against a tape-measured reference (a recorded measurement, not a claim made in advance).

**Risks.** CPU cost: measure it, and note that Depth Anything needed about 160 ms per frame in `distance_estimator`, so it shares cores with detection at a real cost. MiDaS quality degrades at night and on textureless ground; the acceptance gate rejects those frames rather than reporting nonsense. Depth Anything on an outdoor camera gives confident wrong numbers, so the UI warns when it is selected.

### 5.6 Calibration UI and API

**Files.** New `frontend/src/components/calibration/GateLineEditor.tsx`; `ZoneEditor.tsx` (add it to the calibration tab; the component is already large, so do not grow it further); `frontend/src/lib/api.ts` (`CameraCalibration` type).

**Fields.** Draw two points for the gate line; toggle the inside side (arrow on the line); select the linked entrance zone; camera height in metres; **depth model** dropdown (Off, MiDaS, Depth Anything for closed areas).

**API.** Unchanged: `PUT /api/v1/cameras/{id}` and `/api/v1/parking/cameras/{id}` with `calibration`. Add `GET /api/v1/cameras/{id}/calibration/check` (operator-visible, surveillance and parking) returning:

```json
{"has_ground_plane": true, "has_gate": true, "gate_length_m": 6.2,
 "camera_pose": {"ground_x_m": 1.2, "ground_y_m": -8.4, "height_m": 4.1, "source": "pnp", "reprojection_error_px": 1.8},
 "gate_inside_hull": true, "depth_backend": "off", "depth_available": false, "warnings": ["gate line extends outside the calibrated area"]}
```

**Audit.** Calibration changes are already covered by `camera.update` (`changed` lists `calibration`).

**Tests.** Node tests for the pure geometry helpers (normalize, hull test), following the `lib/*.test.ts` pattern with explicit `.ts` imports; API test for `check`; operators get the read-only view.

**Acceptance.** An admin can calibrate a gate end to end in the browser and sees the measured gate length.

### 5.7 Overlay, and parking cameras

**Files.** `frontend/src/lib/api.ts` (`DetectionOverlay`), `frontend/src/components/LiveFeed.tsx`, `services/parking/app/services/pipeline.py`.

**Overlay.** Show `12.4 m · 1.8 m/s · ETA 7 s` when `showGeometry` is on, a trailing `~` plus muted colour when `distance_source` is `depth_estimate`, and the gate line. Never render ETA for a receding track.

**Parking.** Build one `CameraGeometry` per calibrated camera in the pipeline, and call `locate` then `update_gate_state` for vehicles and persons. Do not add risk scoring there (F7). Gate cameras are short-range and this feeds the display and step 6.11's timing only.

**Tests.** Payload contract test for both services; overlay helper tests.

**Acceptance.** Browser check on a calibrated camera; empty fields render nothing.

### Phase 5 done when

All steps above pass, depth is off by default, `docs/architecture.md` gets its geometry section in Phase 7, and a site clip shows the distance within the stated σ of tape measurements.

---

## Phase 6: face recognition service

**Goal.** `services/face`: internal only, own database, InsightFace behind a pluggable embedder. Uses: gate second factor, vehicle-theft check, watchlist ("possible match — verify", never automatic action), named people on the Security Console. Compliant with India's DPDP Act 2023.

**Port, do not copy.** Prototype gaps and what replaces them:

| Prototype (`facial-recognition/backend`) | Port |
|---|---|
| No authentication on any route, including enroll, list and delete | mTLS plus scoped service tokens; no public route |
| `EMBEDDING_ENCRYPTION_ENABLED` flag; stores plaintext if `cryptography` is missing; `decrypt_embedding` accepts legacy plaintext | No flag, no plaintext path; fails closed |
| Key = `SHA-256(SECRET_KEY)`, default `"dev_secret"` | Random KEK file from `deploy/pki.sh`; per-person DEKs |
| No AAD, no key id | AAD binds tenant, person, template, model; KEK id via `SecretBox` |
| `custom_pt_embedder.py` falls back to bare `torch.load` | Removed. ONNX only, or `weights_only=True` with no fallback |
| `joblib.load` for anomaly models | Removed entirely |
| Ultralytics `.pt` anti-spoof backend | Removed; ONNX backend only |
| `FaceAnalysis` downloads its pack at runtime | Offline, SHA-pinned, read-only root |
| Per-frame best-of-all-users aggregation (F8) | Per-identity aggregation |

### 6.0 Prerequisites (before writing code)

Owner actions: a privacy notice drafted (a notice is required before consent under s.5 of the Act); a named grievance contact. `docs/privacy-dpdp.md` (step 7.3) is drafted alongside this phase. Not legal advice: check the Act and the notified DPDP Rules with counsel.

### 6.1 Scaffold the service

**Files.** `services/face/{pyproject.toml,pytest.ini,alembic.ini,Dockerfile,main.py}`, `services/face/app/{config.py,database.py,mesh.py,models.py,schemas.py,utils.py,main.py}`, `services/face/app/routers/{internal.py,health.py}`.

**Reuse.** `argus_common` for the mesh, events (outbox/inbox), audit and secretbox. Follow `services/parking` as the template. Keep `face` out of `argus_vision`.

**Mesh** (`app/mesh.py`), the receiver-side policy:

```python
PEER_SCOPES = {
    "surveillance": {"face:identify", "face:admin", "events:publish"},
    "parking": {"face:verify", "events:publish"},
}
```

`deploy/pki.sh` already issues `face` certs and peers. Surveillance's `PEER_SCOPES["face"]` already has `jwks:read`; add `events:publish`.

**Deploy.** New `deploy/nginx/templates/40-face-internal.conf.template` (mTLS listener, forwards only `/internal/*`); `face` and `face-db` in `docker-compose.yml`; new `deploy/compose/docker-compose.face.yml`; add the edge to `deploy/check.sh`. Add `face` to the CI matrices (`python-services`, `images`) and to Dependabot. **The gateway gets no route to face at all.** Memory limit about 2 GB, `pids_limit`, read-only rootfs.

**Tests.** Health, service-token enforcement, wrong-scope refusal, `/internal/*` needs the mTLS header (copy from parking's `test_service_split.py`).

**Acceptance.** `docker compose up face` is healthy; the image smoke test passes in CI; the public gateway returns 404 for anything face-shaped.

**Risks.** `insightface` needs a compiler at build time and pulls heavy packages; build in the `deps` stage and pin it. The image has never been built, so expect to iterate.

### 6.2 Template encryption

**Files.** `services/face/app/crypto.py`; `deploy/pki.sh` (extend the camera-secrets block to create `face/template-kek.key`).

**Design.** Envelope encryption, AES-256-GCM:

- **KEK:** `FACE_TEMPLATE_KEK_FILE`, loaded by the existing `load_secret_box` (key ids, previous-keys directory and re-seal support come for free).
- **DEK:** 32 random bytes per person, stored in `person_keys.wrapped_dek = box.seal(b64(dek), aad=f"argus-face:dek:v1|{tenant}|{person}")`.
- **Template:** `nonce(12) ‖ AESGCM(dek).encrypt(nonce, float32_le_bytes, aad)`, where `aad = f"argus-face:template:v1|{tenant}|{person}|{template_id}|{model_version}|{dim}"`.

**Functions.** `seal_template(dek, vector, ctx) -> bytes`; `open_template(dek, blob, ctx) -> np.ndarray` (validates dimension and unit norm); `new_person_key(box, tenant, person)`; `destroy_person_key`. Startup re-seals wrapped DEKs under a rotated KEK, like `seal_stored_stream_urls`.

**Fail closed.** No enable flag, and no plaintext branch. A missing or unreadable KEK stops startup, also in DEBUG (tests inject a key explicitly). Any auth-tag failure raises and logs the template id; it never skips silently.

**Tests.** Round trip; each AAD field, swapped between rows, fails; tampered ciphertext fails; a wrong KEK fails; key rotation re-seals and old templates still open; no code path returns plaintext without a valid key; `grep` test that `json.dumps` of vectors is absent.

**Acceptance.** A database dump contains no vector readable without the KEK. Copying a template row onto another person fails to open.

**Risks.** Losing the KEK loses every template: add it to the key table in `SECURITY.md`. Re-enrollment is the only recovery.

### 6.3 Data model

**Files.** `services/face/app/models.py`, `services/face/alembic/versions/0001_baseline.py`. Migrations are the only schema source here (no `create_all`; do not repeat correction C1).

```python
class Person:          # persons
    id, tenant_id, display_name (SealedString), external_ref (SealedString, nullable),
    category  # "resident" | "staff" | "visitor" | "watchlist"
    status    # "active" | "suspended" | "erased"
    is_minor: bool, created_by, created_at, updated_at, erased_at

class PersonKey:       # person_keys (1:1)
    person_id (PK, FK), tenant_id, wrapped_dek, created_at, destroyed_at

class ConsentRecord:   # consents
    id, tenant_id, person_id,
    purpose  # "gate_access" | "vehicle_theft_check" | "console_naming"
    notice_version, method  # "written" | "kiosk" | "guardian"
    evidence_ref, given_at, expires_at, withdrawn_at, recorded_by

class FaceTemplate:    # face_templates
    id, tenant_id, person_id, blob (LargeBinary), model_name, model_version, dim,
    is_centroid, quality_score, created_at

class WatchlistEntry:  # watchlist_entries
    id, tenant_id, person_id,
    legal_basis      # NOT NULL enum: "consenting_test_subject" | "court_order" | "police_request" | "legitimate_use_s7"
    legal_basis_ref  # NOT NULL: order number, request reference, or written assessment
    reason, approved_by, approved_at, review_by, expires_at (NOT NULL, ≤ 180 days)
    revoked_at, revoked_by

class AuditEvent(AuditEventMixin, Base): ...
class OutboxEvent(OutboxEventMixin, Base): ...
class InboxEvent(InboxEventMixin, Base): ...
```

Index `(tenant_id, status)`, `(tenant_id, person_id, purpose)`. A check constraint enforces that `is_minor` persons have consent method `guardian` (s.9); enforce it in the service as well.

**Tests.** Alembic round-trip; a watchlist entry without a legal basis is refused at the schema and database levels; cross-tenant FK use is refused.

### 6.4 Pluggable embedder and model pinning

**Files.** `services/face/app/embedding/{base.py,insightface_embedder.py,stub.py}`, `services/face/models.lock.json`, `NOTICE`.

```python
class Embedder(Protocol):
    model_name: str; model_version: str; dim: int
    def detect(self, image_bgr) -> list[DetectedFace]: ...   # bbox, score, landmarks, normed embedding
```

Selected by `FACE_EMBEDDER=insightface|stub`. Load with an explicit, read-only `root` and `allowed_modules=["detection", "recognition", "landmark_2d_106"]`. Before constructing anything, verify every file in the pack against `models.lock.json`; refuse if a file is missing, since `FaceAnalysis` otherwise tries to download. ONNX only (the InsightFace packs are ONNX), CPU provider.

**Licence gate.** InsightFace code is MIT but the pretrained packs are non-commercial research only. Startup refuses `FACE_EMBEDDER=insightface` unless `FACE_ACK_NONCOMMERCIAL_WEIGHTS=true`.

**Tests.** Hash mismatch refuses; missing acknowledgement refuses; the stub embedder (deterministic vectors keyed by image hash) drives all other tests; real-model test marked `requires_model`.

**Acceptance.** With no network, the service starts from the mounted pack, or refuses clearly. `grep -r "joblib\|pickle\|torch.load" services/face/app` is empty.

**Risks.** Changing the embedder invalidates templates: `model_version` is part of the AAD and of matching, and a version change requires re-enrollment.

### 6.5 Quality, liveness and anti-spoof

**Files.** `services/face/app/quality.py`, `services/face/app/liveness.py`, `services/face/app/antispoof.py` (ports of `quality_filter.py`, `liveness_detector.py`, the ONNX path of `antispoof_detector.py`).

**Rules.** Keep the prototype's gates as settings: blur (Laplacian variance ≥ 100), brightness range, face width ≥ 80 px, yaw ≤ 30°, pitch ≤ 25°. Enrollment is strict (5–10 images). CCTV probes are looser but recorded in the response.

Blink and head-movement liveness needs close-range video, so use it for kiosk enrollment only. At gates, use MiniFASNetV2 ONNX (`ANTISPOOF_ENABLED`, SHA-pinned, off until validated on site).

**Tests.** Table-driven per gate; the anti-spoof model is stubbed; a failed check never reaches matching.

**Risks.** Faces through a windscreen will often fail these. See 6.11; a failure means `verify`, not `deny`.

### 6.6 Matching

**Files.** `services/face/app/matching.py`, `services/face/app/gallery.py`.

**Gallery.** Decrypted vectors are held only in process memory, in one matrix per tenant and purpose, rebuilt from the database. About 2 KB per template (512 × float32), so 10,000 templates is about 20 MB. Run one uvicorn worker. If that ever changes, add a `gallery_version` row polled every 5 s. Never write vectors to disk or logs.

**Maths** (kept from the prototype, cosine on unit vectors):

```
S(q, p)  = 0.7·(q·c_p) + 0.3·max_i(q·e_{p,i})           c_p = person's centroid
T_p      = clamp(mean(pairwise) − 1.5·std(pairwise), T, T + 0.20)   personal threshold
```

Multi-frame, **per identity** (F8): for each person, take the median of that person's top-3 frame scores; require at least 3 valid frames. Then `match` needs `best ≥ T_p` and `best − second ≥ 0.08` (ambiguity rejection).

**Decision bands.** `T_match` and a lower `T_possible`. Start from the prototype's 0.45, but that was tuned for close-range lockers. Re-derive on site footage: pick `T_match` for FMR ≤ 10⁻⁴ per comparison.

**Why gate access uses `verify`.** Identification errors scale with gallery size: `FPIR ≈ 1 − (1 − FMR)^N ≈ N·FMR`. At FMR 10⁻⁴ with 1,000 enrolled, about 1 probe in 10 false-matches *someone*. Verifying against the 1–3 drivers linked to a plate gives about 10⁻⁴ to 3·10⁻⁴. The gate should verify. Open-set identification is only for a small watchlist.

**Tests.** The score formula; personal threshold clamps; F8 regression (five frames matching five people → no match); ambiguity rejection; only persons with consent for the given purpose are in that purpose's gallery; watchlist gallery is separate.

### 6.7 Internal API, with an audit row for every identify call

**Files.** `services/face/app/routers/internal_face.py`, `services/face/app/services/{identify.py,enroll.py}`. Body limit 2 MiB at the edge; at most 8 JPEG frames, 256 KiB each, decoded with a pixel-count limit.

```
POST /internal/v1/face/verify          scope face:verify     (parking)
  {"tenant_id","request_id","purpose":"gate_access","camera_id","candidate_person_ids":["..."],"frames":["<b64 jpeg>"]}
  → {"decision":"match|no_match|no_face|low_quality|no_consent","person_id":"..","label":"..",
     "score":0.61,"threshold":0.52,"margin":0.2,"frames_used":4,"model_version":".."}

POST /internal/v1/face/identify        scope face:identify   (surveillance)
  {"tenant_id","request_id","purpose":"console_naming|watchlist","camera_id","track_id","frames":[...]}
  → {"decision":"match|possible_match|no_match|no_face|low_quality", ...}

POST   /internal/v1/face/persons                  scope face:admin
GET    /internal/v1/face/persons?cursor=
POST   /internal/v1/face/persons/{id}/templates   (5–10 images; needs a live consent first)
POST   /internal/v1/face/persons/{id}/consents
POST   /internal/v1/face/persons/{id}/consents/{cid}/withdraw
GET    /internal/v1/face/persons/{id}/export
DELETE /internal/v1/face/persons/{id}             (erasure)
POST|GET|DELETE /internal/v1/face/watchlist[/{id}]
```

Service tokens carry no tenant, so `tenant_id` is asserted by the calling service, exactly as it is for events today. A compromised peer could therefore query any tenant; record this in the threat model. The acting user travels in an `actor` field filled in by surveillance after its own `require_admin`.

**Audit.** Every `verify` and `identify` writes a `face.verify` or `face.identify` row **whether or not it matched**: caller (`service:parking`), purpose, camera, track, decision, score, threshold, model version, person id when matched. Never the embedding or image. Probes are discarded after the response. Also audited: `face.person.*`, `face.template.enroll`, `face.consent.*`, `face.watchlist.*`, `face.person.erase`, `face.person.export`.

**Rate limit.** `FACE_IDENTIFY_MAX_PER_MIN` per caller and camera, to stop the API being used as a search engine.

**Tests.** Each endpoint's scope; audit row per call (including `no_face`); no image or vector in any audit row or log (scan captured logs); oversized body refused; malformed JPEG handled; cross-tenant refused; parking cannot call `identify`, and surveillance cannot call `verify`.

**Acceptance.** Row count in `audit_events` equals the number of identify and verify calls in a load test.

### 6.8 Consent, erasure, retention

**Files.** `services/face/app/services/{consent.py,erasure.py,retention.py}`.

- **Consent** is per purpose. Enrollment, and inclusion in a purpose's gallery, need a live consent. Withdrawal must be as easy as giving (s.6). It removes the person from that purpose's gallery within 5 s.
- **Erasure** (`DELETE person`), in one transaction: delete templates; set `person_keys.destroyed_at` and overwrite `wrapped_dek`; clear `display_name` and `external_ref`; set `status="erased"`; revoke watchlist entries; audit; enqueue `face.person_erased`. Consent records are kept, without the name, as evidence of lawful processing. Audit rows keep only the opaque person id.
- **Backups.** Erased data survives in database backups until they age out. Keep face DB backups to 30 days at most and state this in the notice. A restore must re-apply erasures, so record erased ids in a small file kept outside the database backup.
- **Retention worker:** consent expired for more than 30 days → erase; `visitor` category → 30 days by default; audit rows → `AUDIT_RETENTION_DAYS`; possible-match crops → `FACE_ALERT_CROP_TTL_HOURS=72`.
- **Access right** (s.11): `GET /export` returns the record and processing history, never vectors.

**Tests.** Withdrawal removes from the gallery; after erasure no template opens, and `identify` cannot match; `face.person_erased` reaches both peers; retention boundaries; an expired consent stops matching before the worker has run (checked at gallery build and on expiry).

**Acceptance.** An end-to-end erasure test: enroll → match → erase → no match → no recoverable data in the live database.

### 6.9 Watchlist (on by default, global off switch; D2)

**Files.** `services/face/app/routers/watchlist.py`, the `identify` watchlist path.

A watchlist entry cannot exist without `legal_basis`, `legal_basis_ref`, `approved_by` and `expires_at`. Creation needs an admin, and ideally a second approver (two-person rule): `approved_by != created_by`.

A hit produces `decision: "possible_match"` only and emits `face.watchlist_possible_match`. The UI text is always "Possible match — verify". **It never sets `AuthDecision.threat`, never affects the gate decision by itself, never moves the risk score, and never triggers an actuator.** The sealed crop is kept for the TTL so that a human can compare.

`FACE_WATCHLIST_ENABLED=true` by default. Switching it off stops watchlist matching everywhere on restart, without deleting entries. Startup logs which state is active.

**Tests.** Flag off → path disabled (404) and no gallery loaded, entries preserved; expired entries are not matched; no code path from a watchlist hit reaches `GateActuator`, asserted structurally.

### 6.10 Surveillance integration

**Files.** New `services/surveillance/app/services/face_client.py`, `face_identity.py`, `face_sampler.py`, `app/routers/people.py`. Changes in `app/mesh.py`, `app/services/event_handlers.py`, `argus_common/events.py`, `app/models.py` (add `face_enabled` to `Camera`; migration guarded per the gotcha).

**Events** (add to `argus_common/events.py`, with `extra="forbid"`; **never** images; the 64 KiB cap applies): `face.identified {camera_id, track_id, person_id, label, score}`, `face.watchlist_possible_match {alert_id, camera_id, track_id, person_ref, score, crop_ref}`, `face.person_erased {person_id}`, `gate.decision_made` (6.11).

**`FaceIdentityCache`** implements `IdentityProvider` (F6): `identify()` reads a dictionary keyed `(tenant, camera, track)` with a 60 s TTL and returns `AuthDecision(authorized=True, source="face", label=name)`. It never does I/O. Register it with `authorization_registry.register_identity_provider`. Providers are consulted last in `resolve`, which is correct.

**`FaceSampler`** selects at most one frame per track per 2 s, best face size first, for cameras with `face_enabled` only, through a bounded queue that drops when full, then calls `identify` off the frame loop.

**People API** for the dashboard, in surveillance so the browser never reaches face: `/api/v1/people/*`, `require_admin` for all (`GET` included), which forwards over mTLS, with each request also audited in surveillance.

**Tests.** Cache TTL and eviction on `face.person_erased`; a face-service outage cannot block the frame loop or change other decisions; sampler bounds; event contracts; `identify()` makes no network call (asserted with a transport that raises).

**Risks.** CCTV faces are small. Expect low recognition rates on wide cameras: enable `face_enabled` only where faces are at least 80 px.

### 6.11 Gate decision, replaceable second factor, actuator stub

**Files.** New `services/parking/app/services/gate_decision.py`, `gate_second_factor.py`, `gate_actuator.py`, `app/routers/gates.py`, migration `0005_gate_decisions`. Change `gate_ocr.py` (`_evaluate_async`), and add a `vehicle_person_links` table.

**Replaceable second factor (D3).** The decision logic never sees "face", only a result. Which method a site uses depends on the site: face for a private building, a FASTag reader for a public lot.

```python
@dataclass(frozen=True)
class SecondFactorResult:
    outcome: Literal["match", "no_match", "unavailable"]
    method: str                  # "face", later "fastag"
    subject_id: Optional[str]
    detail: Optional[str]        # no_face, low_quality, timeout, ...

class GateSecondFactor(Protocol):
    """Replaceable: face today; a FASTag/RFID reader can implement this later."""
    name: str
    binds: Literal["driver", "vehicle"]
    async def check(self, ctx: GateContext) -> SecondFactorResult: ...

FACTORS = {"face": FaceSecondFactor}   # GATE_SECOND_FACTOR=face|none
```

`GATE_SECOND_FACTOR=face` is the default. An unknown value stops startup, so `fastag` cannot be selected until a real reader implementation exists; do not add a fake one.

**Adding FASTag later.** One class plus one column: `FastagSecondFactor` reads the tag's EPC from a UHF reader on the lane and compares it with a new `VehicleProfile.fastag_epc`, with `binds="vehicle"`. A FASTag proves which *tag* passed, not who is driving, and a stolen car still carries its tag, so the theft check (6.12) only runs with a `binds="driver"` factor.

**Data.**

```python
class VehiclePersonLink: id, tenant_id, vehicle_id (FK), person_id, relation, created_by, created_at
class GateDecision:
    id, tenant_id, camera_id, direction, plate, profile_type, decision, reasons (JSON),
    face_result (JSON: decision, score, person_id), actuator_status,
    resolved_by, resolved_at, resolution, created_at
```

**Decision table** (`decide(...)` is a pure function; test every row).

| Plate profile | Second factor | Decision |
|---|---|---|
| blacklist | any | `deny` |
| vip / resident / staff | `match` to the linked person or vehicle | `allow` |
| vip / resident / staff | `no_match` | `verify` |
| vip / resident / staff | `unavailable` (no face, low quality, no consent, service down, timeout) | `verify` (a guard decides, D3) |
| normal or unknown | any | `verify` |
| no plate read | any | `verify` |

`allow` is reachable only through a positive match. A timeout (`FACE_VERIFY_TIMEOUT_S=2.0`) is never an allow. With `GATE_SECOND_FACTOR=none`, an authorized plate may be `allow` on the plate alone, and the row records `second_factor_skipped`.

**Flow in `_evaluate_async`.** Plate read, then the configured `GateSecondFactor` (for face: choose frames from a short ring buffer of the track, look up linked persons, call `verify`), then `decide`, then, in one transaction: write `GateDecision`, audit `gate.decision`, publish `vehicle.gate_passed` (already has `decision` and identity fields) and `gate.decision_made`. Run the actuator **after** the commit, using its outcome to update `actuator_status`.

**Actuator.**

```python
class GateActuator(Protocol):
    async def open(self, decision_id: str, gate_id: str) -> ActuatorResult: ...

# NoopActuator (default) | LoggingActuator | SignedWebhookActuator
```

`SignedWebhookActuator` posts canonical JSON with `X-Argus-Signature: ed25519=<sig>`, signed by parking's service key over `timestamp + "." + body`, and includes `decision_id` as a nonce for replay rejection on the receiver; the URL is validated through `validate_stream_target`-style policy in `argus_common.net`; timeout 2 s, no retries for `open`. Selected by `GATE_ACTUATOR=noop|logging|webhook`. Only `allow` ever calls `open`.

**API.** `GET /api/v1/parking/gates/decisions` (operators), `POST /api/v1/parking/gates/decisions/{id}/resolve {"resolution":"allow|deny","note":".."}` (operators; audited; a resolved `allow` calls the actuator).

**Tests.** All decision-table rows; a fake factor proves `decide` needs no face code; an unknown `GATE_SECOND_FACTOR` refuses to start; timeout gives `verify`; no actuator call for anything except `allow`; signature verifies and tampering fails; the commit is rolled back as a whole; a resolve can only happen once (atomic claim like `ParkbotProposal`).

**Acceptance.** Simulated clips for each row produce the right `GateDecision`. With the default actuator, no external side effect occurs.

**Risks.** Physical safety: closing onto a vehicle is outside this scope. The stub is a decision signal; a real controller must keep its own safety interlocks.

### 6.12 Vehicle-theft check

**Files.** `gate_decision.py` (exit path); `services/parking/app/services/events.py` (`publish_theft_suspected` is finally called, F4).

**Rule at `gate_exit`** (only with a `binds="driver"` factor). Authorized profile, face result `no_match` against linked persons, score below `T_possible`, and at least 3 valid frames → `verify`, publish `vehicle.theft_suspected` with reason "driver not recognized at exit — verify". `no_face` or `low_quality` → `verify`, **without** a theft event: bad images are not evidence. Repeats for the same plate are suppressed for 10 minutes. No automatic blocking.

**Tests.** Each row; no event on low quality; suppression window; the event reaches surveillance and shows as an alert (the handler exists).

**Risks.** Family members and valets who are not enrolled generate false positives. Wording stays "verify".

### 6.13 Frontend

**Files.** New `frontend/src/app/(dashboard)/people/page.tsx`, `components/people/{PersonTable,PersonForm,ConsentPanel,EnrollDialog,WatchlistPanel}.tsx`, `components/parking/GateDecisions.tsx`, `lib/people.ts`. `Sidebar.tsx` gets an `adminOnly` entry (the mechanism exists).

- People: list, create, consent per purpose (the enroll button is disabled until a consent exists), enroll, withdraw, export, erase (typed confirmation).
- Security Console: show `auth_label` next to a track when `auth_source === "face"`.
- Watchlist alerts: always the words "Possible match — verify", with both the enrolled image reference and the crop, and Confirm / Dismiss, audited.
- Gate panel: pending `verify` decisions with Allow / Deny.

**Tests.** Pure helpers; role hiding; a watchlist label test that the required wording is always used.

### 6.14 Close-out

Update `docs/threat-model.md` (§6 becomes real controls), add the KEK to `SECURITY.md`, add the face image to the Trivy and smoke-test matrices, run a security review of `services/face`, and walk the erasure end to end on a deployed stack.

### Phase 6 done when

- `services/face` has no public route, and every identify and verify call has an audit row.
- Enroll → gate allow; unenrolled driver → verify; blacklisted plate → deny; theft check → alert; erase → no match.
- With the service stopped, gates produce `verify`, and surveillance keeps running.
- Plain `grep` finds no joblib, pickle or unsafe `torch.load`, and no plaintext embedding path.

---

## Phase 7: finish

### 7.1 `README.md`
Rewrite for the three-plus-one services: what it is, screenshots, quick start (`deploy/pki.sh init`, `scripts/generate-secrets.sh`, `docker compose up`), links to the docs. **Test:** a clean clone following only the README reaches a healthy stack. **Acceptance:** no reference to removed paths (`backend/yolo_classifier`, `nginx/`).

### 7.2 `docs/architecture.md`, `docs/deployment.md`
Architecture: service diagram, data ownership per database, event catalogue (from `EVENT_SCHEMAS` in `argus_common/events.py`), geometry maths, the mesh. Deployment: single host and split hosts, sizing (the `mem_limit` table), firewall, backups, restore drill, TLS, upgrades. Move or supersede what is in `plans/`. **Acceptance:** a second person can deploy from it; every file path in the docs exists (script this check).

### 7.3 `docs/privacy-dpdp.md`
Role of Data Fiduciary; data inventory (video, plates, faces, logs, audit); purpose and lawful basis per processing (s.4, s.6, s.7); notice template (s.5); consent lifecycle and withdrawal; rights (s.11–13) and how each is executed; children (s.9); retention table; breach response (s.8(6)); processors. Note that the Act has no separate "sensitive data" class, but treat biometrics as the highest tier anyway. Mark clearly that counsel must review it. **Acceptance:** every retention value matches a setting.

### 7.4 `NOTICE` and licence
Add `NOTICE`: InsightFace (code MIT; **pretrained models non-commercial**), Ultralytics YOLO (**AGPL-3.0**: weights and any derived model; distribution or network service obligations, or an Ultralytics enterprise licence), MiDaS (MIT), deep-sort-realtime, OpenVINO (Apache-2.0), MediaMTX (MIT). Verify each licence at the pinned version when writing it. Fill in `LICENSE` once the licence question (§0.1) is settled, and note that AGPL may constrain the choice. **Acceptance:** no model file ships without its notice.

### 7.5 Sibling repo cleanup
Done in each repo, on its own branch, with its own PR. **Never through the argus repo.**

- `C:\dev\distance_estimator`, branch `chore/argus-pointer`: add a README section "Superseded for Argus deployments; see github.com/AakashBhat1/argus", noting that its metric model is indoor-only. Committed `__pycache__` and `egg-info` are already gone (F3); confirm `.gitignore` covers them.
- `C:\dev\facial-recognition`, branch `chore/argus-pointer` off `master`: the same pointer, plus a **security warning** that this prototype has no authentication and must not be exposed. Its gaps are listed in the Phase 6 table. No tracked artefacts to delete (F3).

**Acceptance.** Both PRs are open and their diffs are limited to the README and `.gitignore`.

### 7.6 Final CI
All jobs green on `main`, including Trivy made blocking (C4). Tag `v1.0.0` only if the owner wants a release.

---

## Corrections still owed from Phase 4

| # | Item | Why it was not done | Plan | Acceptance |
|---|---|---|---|---|
| C1 (4.7) | Surveillance schema comes from `init_db` `create_all`; the Alembic baseline is stamp-only | A full baseline against live data needs care and its own PR | New squashed baseline for empty databases; existing databases stamped at head; drop `_ensure_columns` and `create_all` outside tests | A fresh database built by Alembic matches the models (`alembic check`) |
| C3 (4.4) | CSP allows `'unsafe-inline'` scripts | Needs Next.js middleware and removing static prerendering for dashboard routes | Nonce per request in `middleware.ts`; drop `'unsafe-inline'` from `script-src`; keep it for `style-src` at first | No CSP violations in a browser walk-through |
| C4 | Trivy is report-only | Baseline not reviewed; 2 HIGH in setuptools' vendored `jaraco.context` on the Python images | Review; after C5, set `exit-code: 1` and remove `continue-on-error` | A known-vulnerable base fails the job |
| C5 | setuptools pinned at 80.9.0, with two ignored advisories | `deep-sort-realtime` imports `pkg_resources`, removed from setuptools ≥ 81 (and 83.0.0 / 84.1.0 hold the fixes) | Replace the `pkg_resources` use: vendor the small tracker module or find a maintained fork | `--ignore-vuln` entries removed from `ci.yml` |
| C6 (4.9) | Usernames and IPs in application logs | A policy decision | HMAC usernames and truncate IPs in app logs; edge access log with anonymized `$remote_addr` | Log scan test finds no raw usernames |
| C7 (1.3) | `passlib` is unmaintained (no advisory today) | Not a finding | Move to `bcrypt` directly or `argon2-cffi`; verify old hashes, rehash on next login | Existing users sign in; hashes upgrade |
| C8 | `starlette.testclient` with `httpx` is deprecated in Starlette 1.x (warning in test runs) | Warning only | Move to the recommended client when bumping httpx | No deprecation warning |
| C9 (1.1) | ruff runs `F` only | Spec: later | Widen to `E,W,B,S`, fixing or `# noqa`ing each with a reason | Gate green with wider rules |
| C10 | Browser end-to-end from `PHASE4_REMAINING.md` §5.5 | Not yet written | Playwright against a local `docker compose` stack: sign in, refresh, WS reconnect, cross-origin refused, logout, playback owner vs other, audit 403 for operators | Passes locally, then on a CI schedule |
| C12 | Grants are in-memory: lost on restart, not shared between workers | Existing design (the registry docstring says so) | Move to the database or Redis before running more than one worker | Grant survives a restart |
| C13 | gitleaks image digest is not bumped by Dependabot | It runs in a shell step, not as an action | Check quarterly, or adopt a pinned action | Documented owner |
| C14 | CodeQL "note"-level findings (12) not triaged | Not gating | Triage in one pass | Zero untriaged |

## Definition of done (whole project)

1. Every step's acceptance criteria above are met, or moved here with a reason.
2. All suites pass; Alembic round-trips for all services; `deploy/check.sh` passes; every CI job is green on `main`, with Trivy blocking.
3. C1 and C3 are closed before any public launch.
4. Face: no public route, no plaintext template path, every identify audited, consent and erasure proven end to end, no automatic action from any watchlist hit.
5. No runtime model downloads anywhere. Every model is SHA-pinned and listed in `NOTICE`.
6. `docs/privacy-dpdp.md` reviewed by counsel; `SECURITY.md` key table complete.
7. A restore drill and a key-rotation drill were carried out once and written down.
8. Sibling repos carry their pointer PRs.

## Verification commands (Git Bash)

```bash
cd packages/argus_common && ARGUS_ENV_FILE= python -m pytest -q -o addopts=""
cd services/<svc> && ARGUS_ENV_FILE= python -m pytest -q -o addopts="" -m "not requires_model"
# Alembic, in each service, on a throwaway SQLite file:
ARGUS_ENV_FILE= DEBUG=true DATABASE_URL=sqlite+aiosqlite:///./alembic-check.db alembic upgrade head   # then downgrade base, upgrade head, delete the file
cd frontend && npm run lint -- --max-warnings=0 && npm run typecheck && npm test && npm run build
ruff check --select F packages services scripts
shellcheck deploy/*.sh scripts/generate-secrets.sh scripts/setup-tls.sh
deploy/check.sh    # needs Docker Desktop running
```

Baselines after Phase 4: argus_common 99 (2 chmod tests fail on Windows only), surveillance 230, parking 111, frontend 11.

## File map

**New**

| Path | Step |
|---|---|
| `packages/argus_vision/argus_vision/gate_kinematics.py` | 5.2 |
| `packages/argus_vision/argus_vision/depth.py`, `models.lock.json` | 5.5 |
| `scripts/dev/convert_midas_openvino.py`, `scripts/dev/compare_depth.py` | 5.5 |
| `frontend/src/components/calibration/GateLineEditor.tsx` | 5.6 |
| `services/face/**` (app, routers, embedding, crypto, alembic, Dockerfile) | 6.1–6.9 |
| `deploy/nginx/templates/40-face-internal.conf.template`, `deploy/compose/docker-compose.face.yml` | 6.1 |
| `services/surveillance/app/services/{face_client,face_identity,face_sampler}.py`, `app/routers/people.py` | 6.10 |
| `services/parking/app/services/{gate_decision,gate_second_factor,gate_actuator}.py`, `app/routers/gates.py`, migration `0005` | 6.11 |
| `frontend/src/app/(dashboard)/people/page.tsx`, `components/people/*`, `components/parking/GateDecisions.tsx`, `lib/people.ts` | 6.13 |
| `docs/architecture.md`, `docs/deployment.md`, `docs/privacy-dpdp.md`, `NOTICE` | 7.2–7.4 |

**Changed**

| Path | Step |
|---|---|
| `packages/argus_vision/argus_vision/geometry.py`, `settings.py` | 5.1, 5.5 |
| `services/surveillance/app/services/risk_engine.py`, `app/config.py` | 5.3, 5.4 |
| `packages/argus_vision/argus_vision/schemas.py` | 5.1 |
| `services/parking/app/services/pipeline.py` | 5.7 |
| `frontend/src/components/{ZoneEditor,LiveFeed,Sidebar}.tsx`, `lib/api.ts` | 5.6, 5.7, 6.13 |
| `packages/argus_common/argus_common/events.py` | 6.10, 6.11 |
| `deploy/pki.sh`, `deploy/check.sh`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.github/dependabot.yml` | 6.1, 6.2 |
| `services/surveillance/app/{mesh.py,models.py,services/event_handlers.py}` | 6.10 |
| `services/parking/app/{mesh.py,services/gate_ocr.py,services/events.py}` | 6.11, 6.12 |
| `SECURITY.md`, `docs/threat-model.md`, `README.md`, `LICENSE` | 6.14, 7.1, 7.4 |
