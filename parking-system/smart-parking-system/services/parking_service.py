"""
Smart Parking System — Parking Space Management Service
CRUD operations for spaces, assignment, release, and statistics.
"""
import datetime
from models import database as db
from services import event_bus
import config as app_config


def get_all_spaces():
    """Get all parking spaces grouped by zone."""
    rows = db.fetchall(
        "SELECT space_id, zone, floor, is_occupied, plate_text, entry_time FROM parking_spaces ORDER BY zone, space_id"
    )
    return [dict(r) for r in rows]


def get_spaces_by_zone():
    """Get spaces organized by zone."""
    spaces = get_all_spaces()
    zones = {}
    for s in spaces:
        zone = s["zone"]
        if zone not in zones:
            zones[zone] = []
        zones[zone].append(s)
    return zones


def get_stats():
    """Get overall parking statistics."""
    total = db.fetchone("SELECT COUNT(*) as c FROM parking_spaces")["c"]
    occupied = db.fetchone("SELECT COUNT(*) as c FROM parking_spaces WHERE is_occupied = 1")["c"]
    free = total - occupied

    today = datetime.date.today().isoformat()
    plates_today = db.fetchone(
        "SELECT COUNT(*) as c FROM detected_plates WHERE timestamp LIKE ?",
        (f"{today}%",)
    )["c"]

    return {
        "total": total,
        "occupied": occupied,
        "free": free,
        "available": free,  # alias for frontend compatibility (back-compat "free" kept)
        "occupancy_pct": round(occupied / total * 100, 1) if total > 0 else 0,
        "plates_today": plates_today,
    }


def get_free_spaces(limit=5):
    """Get a list of free space IDs."""
    rows = db.fetchall(
        "SELECT space_id, zone FROM parking_spaces WHERE is_occupied = 0 ORDER BY zone, space_id LIMIT ?",
        (limit,)
    )
    return [dict(r) for r in rows]


def get_total_spaces():
    """Return current number of parking space rows (live DB count)."""
    row = db.fetchone("SELECT COUNT(*) as c FROM parking_spaces")
    return row["c"] if row else 0


def assign_space(plate_text):
    """Assign the first available space to a plate, routing VIPs to premium Ground Floor spots. Returns space_id or None."""
    # Look up vehicle profile (enriched for real-time events)
    profile = db.fetchone(
        "SELECT profile_type, owner_name, notes FROM vehicle_profiles WHERE plate_text = ?",
        (plate_text,)
    )
    profile_type = profile["profile_type"] if profile else "normal"
    owner_name = profile["owner_name"] if profile else "Visitor"
    notes = profile["notes"] if profile else ""

    if profile_type == "blacklist":
        db.log_activity("security_alert", f"BOLO ALERT: Blacklisted vehicle {plate_text} detected! Notify security.", plate_text)

    row = None
    if profile_type == "vip":
        # Route to nearest Ground floor space
        row = db.fetchone("SELECT space_id FROM parking_spaces WHERE is_occupied = 0 AND floor = 'G' ORDER BY space_id ASC LIMIT 1")
    
    if not row:
        # Fallback to standard search prioritizing G -> 1 -> 2 floors
        row = db.fetchone(
            "SELECT space_id FROM parking_spaces WHERE is_occupied = 0 "
            "ORDER BY CASE floor WHEN 'G' THEN 1 WHEN '1' THEN 2 WHEN '2' THEN 3 END ASC, space_id ASC LIMIT 1"
        )
        
    if not row:
        return None

    space_id = row["space_id"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db.execute(
        "UPDATE parking_spaces SET is_occupied = 1, plate_text = ?, entry_time = ? WHERE space_id = ?",
        (plate_text, now, space_id), commit=True
    )
    db.execute(
        "UPDATE detected_plates SET is_parked = 1 WHERE plate_text = ? AND is_parked = 0",
        (plate_text,), commit=True
    )

    # Update session counters
    session_id = db.get_current_session_id()
    if session_id:
        db.execute(
            "UPDATE sessions SET spaces_used = spaces_used + 1 WHERE id = ?",
            (session_id,), commit=True
        )

    if profile_type == "vip":
        db.log_activity("vip_entry", f"VIP {plate_text} routed to premium spot {space_id}", plate_text, space_id)
    else:
        db.log_activity("space_assigned", f"Plate {plate_text} assigned to {space_id}", plate_text, space_id)

    # Real-time event for all listeners (SSE /api/events)
    event_bus.publish({
        "event": "entry",
        "space_id": space_id,
        "plate_text": plate_text,
        "profile_type": profile_type,
        "owner_name": owner_name,
        "notes": notes
    })

    return space_id


def release_space(space_id):
    """Release a parking space, calculate stay duration and financial tariff."""
    row = db.fetchone(
        "SELECT plate_text, entry_time FROM parking_spaces WHERE space_id = ? AND is_occupied = 1",
        (space_id,)
    )
    if not row:
        return None

    plate_text = row["plate_text"]
    entry_time = row["entry_time"]
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # Calculate duration
    duration_min = 0
    if entry_time:
        try:
            entry_dt = datetime.datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
            duration_min = int((now - entry_dt).total_seconds() / 60)
        except ValueError:
            pass

    # Calculate payment amount based on tariff
    amount = 0.0
    if duration_min >= 0:
        import config
        import math
        rate = getattr(config, "PARKING_RATE_PER_HOUR", 20)
        if duration_min > 5:
            # Hourly billing, rounded up
            amount = float(math.ceil(duration_min / 60.0) * rate)
        else:
            # Under 5 mins is flat test rate
            amount = 10.0

    # Clear the space
    db.execute(
        "UPDATE parking_spaces SET is_occupied = 0, plate_text = NULL, entry_time = NULL WHERE space_id = ?",
        (space_id,), commit=True
    )

    # Update plate record
    if plate_text:
        db.execute(
            "UPDATE detected_plates SET is_parked = 0, exit_time = ?, duration_minutes = ?, amount_paid = ? WHERE plate_text = ? AND is_parked = 1",
            (now_str, duration_min, amount, plate_text), commit=True
        )

    db.log_activity(
        "space_released",
        f"Space {space_id} released (was {plate_text or 'unknown'}, {duration_min} min, paid: {amount} INR)",
        plate_text, space_id
    )

    # Real-time event for all listeners (SSE /api/events)
    event_bus.publish({
        "event": "exit",
        "space_id": space_id,
        "plate_text": plate_text,
        "duration_minutes": duration_min,
        "amount_paid": amount
    })

    return {
        "space_id": space_id,
        "plate_text": plate_text,
        "duration_minutes": duration_min,
        "amount_paid": amount
    }


def record_detection(plate_text, state, confidence):
    """Record a newly detected plate."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO detected_plates (plate_text, state, timestamp, confidence) VALUES (?, ?, ?, ?)",
        (plate_text, state, now, confidence), commit=True
    )

    # Update session counter
    session_id = db.get_current_session_id()
    if session_id:
        db.execute(
            "UPDATE sessions SET plates_detected = plates_detected + 1 WHERE id = ?",
            (session_id,), commit=True
        )

    db.log_activity("plate_detected", f"Detected plate: {plate_text} ({state})", plate_text)


def get_recent_plates(limit=10):
    """Get recently detected plates."""
    rows = db.fetchall(
        "SELECT id, plate_text, state, timestamp, is_parked, confidence FROM detected_plates ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    return [dict(r) for r in rows]


def get_plate_by_id(plate_id):
    """Get a single plate record."""
    row = db.fetchone(
        "SELECT id, plate_text, state, timestamp, is_parked, exit_time, duration_minutes, confidence FROM detected_plates WHERE id = ?",
        (plate_id,)
    )
    return dict(row) if row else None


def get_activity_log(limit=50):
    """Get recent activity log entries."""
    rows = db.fetchall(
        "SELECT id, timestamp, event_type, description, plate_text, space_id FROM activity_log ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    return [dict(r) for r in rows]


def get_session_info():
    """Get current session information."""
    row = db.fetchone("SELECT * FROM sessions ORDER BY id DESC LIMIT 1")
    if not row:
        return None
    info = dict(row)

    # Calculate uptime
    try:
        start = datetime.datetime.strptime(info["start_time"], "%Y-%m-%d %H:%M:%S")
        uptime = datetime.datetime.now() - start
        info["uptime_seconds"] = int(uptime.total_seconds())
        info["uptime_display"] = str(uptime).split(".")[0]  # HH:MM:SS
    except (ValueError, KeyError):
        info["uptime_seconds"] = 0
        info["uptime_display"] = "00:00:00"

    return info


def get_analytics_data():
    """Get heatmap utilization and occupancy predictions."""
    import math
    
    # 1. Heatmap: Count assignments per space
    rows = db.fetchall(
        "SELECT space_id, COUNT(*) as count FROM activity_log WHERE event_type = 'space_assigned' GROUP BY space_id"
    )
    heatmap = {r["space_id"]: r["count"] for r in rows}
    
    # Ensure all spaces are present in the heatmap dictionary
    all_spaces = get_all_spaces()
    for s in all_spaces:
        if s["space_id"] not in heatmap:
            heatmap[s["space_id"]] = 0

    # Build heatmap_cells with explicit floor/zone (do NOT parse space_id for zone;
    # space_ids like "G-01" have no zone embedded; zone is separate A/B/C column).
    heatmap_cells = []
    for s in all_spaces:
        heatmap_cells.append({
            "space_id": s["space_id"],
            "floor": s["floor"],
            "zone": s["zone"],
            "count": heatmap.get(s["space_id"], 0)
        })
            
    # 2. Predictive occupancy: Hour by hour (0-23)
    hourly_entries = [0] * 24
    rows_hr = db.fetchall(
        "SELECT strftime('%H', timestamp) as hr, COUNT(*) as count FROM detected_plates GROUP BY hr"
    )
    for r in rows_hr:
        try:
            hr = int(r["hr"])
            hourly_entries[hr] = r["count"]
        except (ValueError, TypeError):
            pass

    # Generate a smooth predictive baseline (sinusoidal business hours) scaled to lot size
    total_spaces = len(all_spaces) if all_spaces else 24
    predictions = []
    for hr in range(24):
        # Sine wave peaking at 14:00 (2 PM) and bottoming at 04:00 AM
        val = 0.15 + 0.65 * (0.5 + 0.5 * math.sin(math.pi * (hr - 8) / 12))
        # Add small weight from historical entries
        hist_weight = min(hourly_entries[hr] * 0.1, 0.2)
        val = min(max(val + hist_weight, 0.05), 0.95)
        predictions.append(round(val * total_spaces, 1))

    # 3. Actual occupancy of today so far (hour by hour)
    today = datetime.date.today().isoformat()
    actual_today = [None] * 24
    current_hour = datetime.datetime.now().hour
    
    log_rows = db.fetchall(
        "SELECT timestamp, event_type FROM activity_log WHERE timestamp LIKE ? ORDER BY timestamp ASC",
        (f"{today}%",)
    )
    
    # Find active slots before today to set baseline
    pre_today = db.fetchone(
        "SELECT COUNT(*) as c FROM detected_plates WHERE timestamp < ? AND is_parked = 1",
        (f"{today} 00:00:00",)
    )["c"]
    curr_occ = pre_today
    
    hourly_events = {hr: [] for hr in range(24)}
    for row in log_rows:
        try:
            dt = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            hourly_events[dt.hour].append(row["event_type"])
        except ValueError:
            pass
            
    for hr in range(24):
        if hr > current_hour:
            break
        # Process events in this hour
        for ev in hourly_events[hr]:
            if ev == "space_assigned":
                curr_occ = min(curr_occ + 1, total_spaces)
            elif ev == "space_released":
                curr_occ = max(curr_occ - 1, 0)
        actual_today[hr] = curr_occ
        
    return {
        "heatmap": heatmap,
        "predictions": predictions,
        "actual_today": actual_today,
        "current_hour": current_hour,
        "heatmap_cells": heatmap_cells
    }


def get_billing_stats():
    """Calculate revenue ledger and retrieve recent payment transactions."""
    total_rev_row = db.fetchone("SELECT SUM(amount_paid) as sum FROM detected_plates")
    total_revenue = float(total_rev_row["sum"]) if total_rev_row and total_rev_row["sum"] is not None else 0.0

    total_trans_row = db.fetchone("SELECT COUNT(*) as count FROM detected_plates WHERE amount_paid > 0")
    total_transactions = total_trans_row["count"] if total_trans_row else 0

    avg_transaction = round(total_revenue / total_transactions, 2) if total_transactions > 0 else 0.0

    recent_rows = db.fetchall(
        "SELECT plate_text, exit_time, duration_minutes, amount_paid FROM detected_plates "
        "WHERE exit_time IS NOT NULL AND amount_paid > 0 ORDER BY exit_time DESC LIMIT 10"
    )
    recent_transactions = [dict(r) for r in recent_rows]

    return {
        "total_revenue": total_revenue,
        "total_transactions": total_transactions,
        "avg_transaction": avg_transaction,
        "recent_transactions": recent_transactions
    }


def get_billing_export():
    """Return all paid/exited transactions for CSV export (real data only, from detected_plates).
    Columns match the billing history: plate_text, exit_time, duration_minutes, amount_paid.
    Ordered newest first. Used by GET /api/billing/export to serve attachment.
    """
    rows = db.fetchall(
        "SELECT plate_text, exit_time, duration_minutes, amount_paid FROM detected_plates "
        "WHERE exit_time IS NOT NULL AND amount_paid > 0 ORDER BY exit_time DESC"
    )
    return [dict(r) for r in rows]


def get_all_profiles():
    """Retrieve all vehicle profiles from the database."""
    rows = db.fetchall(
        "SELECT plate_text, profile_type, owner_name, notes FROM vehicle_profiles ORDER BY plate_text ASC"
    )
    return [dict(r) for r in rows]


def save_profile(plate_text, profile_type, owner_name, notes):
    """Insert or update a vehicle profile record."""
    db.execute(
        "INSERT OR REPLACE INTO vehicle_profiles (plate_text, profile_type, owner_name, notes) "
        "VALUES (?, ?, ?, ?)",
        (plate_text, profile_type, owner_name, notes),
        commit=True
    )
    db.log_activity("profile_updated", f"Saved vehicle profile for {plate_text} ({profile_type})", plate_text)

    # Real-time event for all listeners (SSE /api/events)
    # Enriched with owner_name + notes on SAVE so frontend delta updates (optimistic profile lists)
    # do not lose data until next full resync. DELETE path stays minimal.
    event_bus.publish({
        "event": "profile_change",
        "plate_text": plate_text,
        "profile_type": profile_type,
        "owner_name": owner_name,
        "notes": notes
    })

    return True


def delete_profile(plate_text):
    """Delete a vehicle profile record from the database."""
    # Capture type for the event before deletion
    prof = db.fetchone("SELECT profile_type FROM vehicle_profiles WHERE plate_text = ?", (plate_text,))
    profile_type = prof["profile_type"] if prof else "unknown"

    db.execute(
        "DELETE FROM vehicle_profiles WHERE plate_text = ?",
        (plate_text,),
        commit=True
    )
    db.log_activity("profile_deleted", f"Deleted vehicle profile for {plate_text}", plate_text)

    # Real-time event for all listeners (SSE /api/events)
    event_bus.publish({
        "event": "profile_change",
        "plate_text": plate_text,
        "profile_type": profile_type
    })

    return True


def _adjust_parking_capacity(target):
    """Internal: safely change DB space count to target (1..200).
    Increase: add only new FREE spaces via db helper (consistent naming).
    Decrease: remove ONLY free spaces; reject if would touch occupied.
    Returns the final total count.
    """
    if not isinstance(target, int) or not (1 <= target <= 200):
        raise ValueError("target must be int between 1 and 200")
    current = get_total_spaces()
    if target == current:
        return current
    if target > current:
        to_add = target - current
        db._add_free_spaces(to_add)
        db.log_activity("system", f"Scaled parking capacity up by {to_add} (target {target})")
    else:
        to_remove = current - target
        free_row = db.fetchone("SELECT COUNT(*) as c FROM parking_spaces WHERE is_occupied = 0")
        free_count = free_row["c"] if free_row else 0
        if to_remove > free_count:
            raise ValueError("cannot shrink below occupied count")
        # Remove free spaces (prefer highest space_id for determinism)
        db.execute(
            """
            DELETE FROM parking_spaces
            WHERE space_id IN (
                SELECT space_id FROM parking_spaces
                WHERE is_occupied = 0
                ORDER BY space_id DESC
                LIMIT ?
            )
            """,
            (to_remove,), commit=True
        )
        db.log_activity("system", f"Scaled parking capacity down by {to_remove} free spaces (target {target})")
    return get_total_spaces()


def reset_parking_session(hard=False):
    """Soft or hard reset of the parking session.
    soft (default): free all spaces, end current session, start fresh session. History preserved.
    hard: additionally DELETE detected_plates + activity_log (full demo wipe).
    Always publishes 'system_reset' SSE event.
    Returns dict for API response.
    """
    # Count what we will free
    occ_row = db.fetchone("SELECT COUNT(*) as c FROM parking_spaces WHERE is_occupied = 1")
    spaces_freed = occ_row["c"] if occ_row else 0

    # Free every space (safe even if none)
    db.execute(
        "UPDATE parking_spaces SET is_occupied = 0, plate_text = NULL, entry_time = NULL",
        commit=True
    )

    # End current session if any
    sid = db.get_current_session_id()
    if sid:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE sessions SET end_time = ? WHERE id = ?", (now_str, sid), commit=True)

    # Start brand new session (logs "System session started")
    db.start_session()

    mode = "hard" if hard else "soft"
    if hard:
        db.execute("DELETE FROM detected_plates", commit=True)
        db.execute("DELETE FROM activity_log", commit=True)
        db.log_activity("system", "Hard reset: spaces freed, session restarted, history cleared")
    else:
        db.log_activity("system", "Soft reset: all spaces freed, new session started (history preserved)")

    # Broadcast so connected frontends can resync everything
    event_bus.publish({"event": "system_reset", "mode": mode})

    return {"spaces_freed": spaces_freed, "mode": mode}


def update_runtime_config(default_spaces=None, rate_per_hour=None):
    """Apply validated config changes in-memory + safe space scaling.
    Mutates module-level config (rate takes effect immediately on next release_space).
    Returns the effective values + total_spaces.
    May raise ValueError on bad shrink (caller turns into 400).
    Also publishes 'config_change' SSE.
    """
    changed = False
    if default_spaces is not None:
        # Will raise on bad value or impossible shrink (caught by route)
        _adjust_parking_capacity(int(default_spaces))
        app_config.DEFAULT_SPACES = int(default_spaces)
        changed = True
    if rate_per_hour is not None:
        app_config.PARKING_RATE_PER_HOUR = float(rate_per_hour)
        changed = True

    current_total = get_total_spaces()
    current_rate = float(getattr(app_config, "PARKING_RATE_PER_HOUR", 20))
    current_default = int(getattr(app_config, "DEFAULT_SPACES", current_total))

    if changed:
        event_bus.publish({
            "event": "config_change",
            "default_spaces": current_default,
            "rate_per_hour": current_rate,
            "total_spaces": current_total
        })
        db.log_activity("system", f"Runtime config updated (default_spaces={current_default}, rate={current_rate})")

    return {
        "default_spaces": current_default,
        "rate_per_hour": current_rate,
        "total_spaces": current_total
    }


def get_config():
    """Return current in-memory config values + live DB total_spaces count.
    Shape mirrors the success body of POST /api/config (without 'success').
    Used by frontend for invoice rate display and admin config pre-fill.
    """
    return {
        "default_spaces": int(getattr(app_config, "DEFAULT_SPACES", 24)),
        "rate_per_hour": float(getattr(app_config, "PARKING_RATE_PER_HOUR", 20.0)),
        "total_spaces": get_total_spaces(),
    }



