"""
Smart Parking System — REST API Routes
"""
from flask import Blueprint, jsonify, request, Response, stream_with_context
import json
import time
import queue

from services import parking_service
from services import event_bus
from services.camera_service import get_latest_plate
from models import database as db

import config as app_config

api_bp = Blueprint("api", __name__, url_prefix="/api")

# INTEGRATION / CORS NOTE (current state as of Round 6):
# The SvelteKit frontend (web/) uses Vite dev proxy (vite.config.ts) that forwards
# /api/* and /feed/* to the Flask backend on localhost:5000. In production a reverse
# proxy (or adapter) will do the same. This keeps everything same-origin in the browser.
# Therefore NO flask-cors is installed or used, and we do not loosen origins.
#
# Exact change that WOULD be required if the frontend ever drops the proxy and
# must call the API cross-origin:
#   1. pip install flask-cors
#   2. In app.py (or here): from flask_cors import CORS
#      CORS(app, resources={r"/api/*": {"origins": "https://EXACT-FRONTEND-ORIGIN"}},
#           supports_credentials=False)   # only if cookies/sessions ever used
#   For the SSE /api/events endpoint: EventSource will require the
#   Access-Control-Allow-Origin response header (flask-cors adds it).
#   Never use origins="*" together with credentials.
# Leave the above OFF for now. Proxy is the canonical path.


def _admin_guard():
    """Optional admin gate (demo/off-by-default).
    If app_config.ADMIN_TOKEN is falsy (default), allow everything (no 401 ever).
    If set, require X-Admin-Token: <exact> header (preferred) or Authorization: Bearer <exact>.
    Returns (response, status) tuple on failure for the caller to return immediately, else None.
    """
    token = getattr(app_config, "ADMIN_TOKEN", None)
    if not token:
        return None  # demo mode / local dev: pass-through exactly as before
    provided = (request.headers.get("X-Admin-Token") or "").strip()
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
    if provided != token:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    return None


@api_bp.route("/stats")
def stats():
    return jsonify(parking_service.get_stats())


@api_bp.route("/spaces")
def spaces():
    all_spaces = parking_service.get_all_spaces()
    stats = parking_service.get_stats()
    return jsonify({"spaces": all_spaces, **stats})


@api_bp.route("/spaces/<space_id>/release", methods=["POST"])
def release_space(space_id):
    result = parking_service.release_space(space_id)
    if result:
        return jsonify({"success": True, **result})
    return jsonify({"success": False, "error": "Space not found or already free"}), 404


@api_bp.route("/plates")
def plates():
    limit = request.args.get("limit", 20, type=int) or 20
    limit = max(1, min(200, limit))  # harden against abuse
    return jsonify(parking_service.get_recent_plates(limit))


@api_bp.route("/plates/<int:plate_id>")
def plate_detail(plate_id):
    plate = parking_service.get_plate_by_id(plate_id)
    if plate:
        return jsonify(plate)
    return jsonify({"error": "Not found"}), 404


@api_bp.route("/latest_plate")
def latest_plate():
    plate = get_latest_plate()
    if plate and plate.get("text"):
        profile = db.fetchone("SELECT profile_type, owner_name, notes FROM vehicle_profiles WHERE plate_text = ?", (plate["text"],))
        if profile:
            plate["profile_type"] = profile["profile_type"]
            plate["owner_name"] = profile["owner_name"]
            plate["notes"] = profile["notes"]
        else:
            plate["profile_type"] = "normal"
            plate["owner_name"] = "Visitor"
            plate["notes"] = ""
    return jsonify(plate)


@api_bp.route("/activity")
def activity():
    limit = request.args.get("limit", 30, type=int) or 30
    limit = max(1, min(200, limit))  # harden against abuse
    return jsonify(parking_service.get_activity_log(limit))


@api_bp.route("/session")
def session():
    info = parking_service.get_session_info()
    if info:
        return jsonify(info)
    return jsonify({"error": "No active session"}), 404


@api_bp.route("/analytics")
def analytics():
    return jsonify(parking_service.get_analytics_data())


@api_bp.route("/events")
def events():
    """SSE real-time event stream.
    Emits on vehicle entry, exit, and profile changes.
    Payloads (see CONTRACT):
      entry: {"event":"entry", "space_id":.., "plate_text":.., "profile_type":.., "owner_name":.., "notes":..}
      exit:  {"event":"exit", "space_id":.., "plate_text":.., "duration_minutes":.., "amount_paid":..}
      profile_change: {"event":"profile_change", "plate_text":.., "profile_type":..}
    Includes periodic :keepalive comments.
    """
    def generate():
        q = event_bus.subscribe()
        try:
            last_keepalive = time.time()
            while True:
                try:
                    event = q.get(timeout=5)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    if time.time() - last_keepalive > 15:
                        yield ":keepalive\n\n"
                        last_keepalive = time.time()
                    # loop to allow clean shutdown on client disconnect
        except GeneratorExit:
            pass
        finally:
            event_bus.unsubscribe(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@api_bp.route("/health")
def health():
    """Liveness + DB connectivity (always HTTP 200).
    db:"ok" or "down" (never errors the endpoint).
    """
    import datetime as _dt
    try:
        row = db.fetchone("SELECT 1 as ok")
        db_status = "ok" if row else "down"
    except Exception:
        db_status = "down"
    return jsonify({
        "status": "ok",
        "db": db_status,
        "time": _dt.datetime.now().isoformat()
    })


@api_bp.route("/sandbox/entry", methods=["POST"])
def sandbox_entry():
    data = request.get_json() or {}
    plate_text = data.get("plate_text", "").strip().upper()
    state = data.get("state", "DL").strip().upper()

    if not plate_text:
        return jsonify({"success": False, "error": "License plate text is required"}), 400

    # Query profile details
    profile = db.fetchone("SELECT profile_type, owner_name, notes FROM vehicle_profiles WHERE plate_text = ?", (plate_text,))
    profile_type = profile["profile_type"] if profile else "normal"
    owner_name = profile["owner_name"] if profile else "Visitor"
    notes = profile["notes"] if profile else ""

    # Record detection
    parking_service.record_detection(plate_text, state, 0.95)
    
    # Assign space
    space_id = parking_service.assign_space(plate_text)
    if space_id:
        return jsonify({
            "success": True,
            "space_id": space_id,
            "plate_text": plate_text,
            "state": state,
            "profile_type": profile_type,
            "owner_name": owner_name,
            "notes": notes
        })
    else:
        return jsonify({"success": False, "error": "No parking spaces available"}), 400


@api_bp.route("/sandbox/exit", methods=["POST"])
def sandbox_exit():
    data = request.get_json() or {}
    space_id = data.get("space_id", "").strip()

    if not space_id:
        return jsonify({"success": False, "error": "Space ID is required"}), 400

    result = parking_service.release_space(space_id)
    if result:
        return jsonify({"success": True, **result})
    return jsonify({"success": False, "error": "Space not occupied or not found"}), 400


@api_bp.route("/billing/stats")
def billing_stats():
    return jsonify(parking_service.get_billing_stats())


@api_bp.route("/billing/export")
def billing_export():
    """Return paid/exited billing transactions as CSV download (attachment).
    Columns: plate_text, exit_time, duration_minutes, amount_paid (real data only).
    Uses Content-Disposition so browsers prompt save-as.
    No admin token gate (per spec; only the three management endpoints are gated).
    """
    rows = parking_service.get_billing_export()
    import csv
    import io
    output = io.StringIO()
    fieldnames = ["plate_text", "exit_time", "duration_minutes", "amount_paid"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (r.get(k) if r.get(k) is not None else "") for k in fieldnames})
    csv_str = output.getvalue()
    resp = Response(csv_str, mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = 'attachment; filename="billing_export.csv"'
    return resp


@api_bp.route("/profiles")
def get_profiles():
    return jsonify({"profiles": parking_service.get_all_profiles()})


@api_bp.route("/profiles", methods=["POST"])
def save_profile():
    data = request.get_json() or {}
    plate_text = data.get("plate_text", "").strip().upper()
    profile_type = data.get("profile_type", "normal").strip().lower()
    owner_name = data.get("owner_name", "").strip()
    notes = data.get("notes", "").strip()

    if not plate_text:
        return jsonify({"success": False, "error": "Plate text is required"}), 400

    if profile_type not in ("normal", "vip", "blacklist"):
        profile_type = "normal"

    success = parking_service.save_profile(plate_text, profile_type, owner_name, notes)
    return jsonify({"success": success})


@api_bp.route("/profiles/<plate_text>", methods=["DELETE"])
def delete_profile(plate_text):
    plate_text = plate_text.strip().upper()
    success = parking_service.delete_profile(plate_text)
    return jsonify({"success": success})


@api_bp.route("/session/reset", methods=["POST"])
def session_reset():
    """Guarded destructive reset (soft or hard).
    Body MUST contain {"confirm": true}. Optional "hard": bool (default false).
    Soft: frees spaces + new session (history kept).
    Hard: also wipes detected_plates + activity_log.
    Publishes system_reset SSE.

    Optional admin gating: if ADMIN_TOKEN is set in env, requires X-Admin-Token header
    (or Authorization: Bearer) matching it; returns 401 otherwise. When token unset (default),
    behaves exactly as before for demo compatibility.
    """
    guard = _admin_guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"success": False, "error": "confirm flag required"}), 400

    hard = bool(data.get("hard", False))
    try:
        result = parking_service.reset_parking_session(hard=hard)
        return jsonify({"success": True, **result})
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400


@api_bp.route("/config", methods=["POST"])
def update_config():
    """Update runtime config (in-memory).
    Accepts {default_spaces?: int 1-200, rate_per_hour?: number >0 }.
    Space scaling is safe (adds free only; rejects shrink that would drop occupied).
    Rate applies live to next release. Publishes config_change SSE.
    Returns the applied values + live total_spaces.

    Optional admin gating: if ADMIN_TOKEN is set in env, requires X-Admin-Token header
    (or Authorization: Bearer) matching it; returns 401 otherwise. When token unset (default),
    behaves exactly as before for demo compatibility.
    """
    guard = _admin_guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    new_spaces = data.get("default_spaces")
    new_rate = data.get("rate_per_hour")

    errors = []
    if new_spaces is not None:
        try:
            new_spaces = int(new_spaces)
            if not (1 <= new_spaces <= 200):
                errors.append("default_spaces must be integer 1..200")
        except (TypeError, ValueError):
            errors.append("default_spaces must be integer 1..200")

    if new_rate is not None:
        try:
            new_rate = float(new_rate)
            if new_rate <= 0:
                errors.append("rate_per_hour must be number > 0")
        except (TypeError, ValueError):
            errors.append("rate_per_hour must be number > 0")

    if errors:
        return jsonify({"success": False, "error": "; ".join(errors)}), 400

    try:
        applied = parking_service.update_runtime_config(
            default_spaces=new_spaces,
            rate_per_hour=new_rate
        )
        return jsonify({"success": True, **applied})
    except ValueError as ex:
        # e.g. cannot shrink below occupied
        return jsonify({"success": False, "error": str(ex)}), 400
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400


@api_bp.route("/config")
def get_config():
    """Read current runtime config (for receipts, pre-fills, etc.).
    Mirrors the success payload shape of POST /api/config.
    """
    return jsonify(parking_service.get_config())


@api_bp.route("/admin/connections")
def admin_connections():
    """Return approximate number of active /api/events SSE subscribers.

    Optional admin gating: if ADMIN_TOKEN is set in env, requires X-Admin-Token header
    (or Authorization: Bearer) matching it; returns 401 otherwise. When token unset (default),
    behaves exactly as before for demo compatibility.
    """
    guard = _admin_guard()
    if guard:
        return guard

    count = event_bus.subscriber_count()
    return jsonify({"active_sse_clients": count})


