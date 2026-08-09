"""
Smart Parking System — AI Assistant Service
Dual-persona Ollama chat with live data context injection.
- User mode: helps find free spaces, answers parking questions
- Admin mode: summarizes session activity, reports analytics
"""
import json
import requests
import datetime
import config
from services import parking_service


USER_SYSTEM_PROMPT = """You are ParkBot, a friendly and helpful AI parking assistant for the Smart Parking System.
You help visitors find free parking spaces, answer questions about parking availability, duration, and rates.
Keep responses concise (2-4 sentences). Be warm and professional.
The parking rate is ₹{rate}/hour.

{context}

Answer based ONLY on the live data above. If you don't have enough info, say so politely.
Do NOT use any thinking tags or internal reasoning in your response. Respond directly."""

ADMIN_SYSTEM_PROMPT = """You are ParkBot Admin, an operations analyst for the Smart Parking System.
You help administrators understand parking activity, generate session reports, and identify trends.
Keep responses concise and data-driven. Use bullet points for clarity.

{context}

Analyze the data above to answer the admin's question. Be precise with numbers.
Do NOT use any thinking tags or internal reasoning in your response. Respond directly."""


def _build_user_context():
    """Build live parking data context for user-mode prompts."""
    stats = parking_service.get_stats()
    free_spaces = parking_service.get_free_spaces(8)
    recent = parking_service.get_recent_plates(3)

    free_list = ", ".join([f"{s['space_id']} (Zone {s['zone']})" for s in free_spaces]) if free_spaces else "None available"
    recent_list = "\n".join([f"  - {p['plate_text']} ({p['state']}) at {p['timestamp']}" for p in recent]) if recent else "  None yet"

    return f"""LIVE PARKING STATUS:
- Total spaces: {stats['total']}
- Free spaces: {stats['free']}
- Occupied: {stats['occupied']}
- Occupancy rate: {stats['occupancy_pct']}%
- Available spaces: {free_list}
- Recent detections:
{recent_list}"""


def _build_admin_context():
    """Build session and activity context for admin-mode prompts."""
    stats = parking_service.get_stats()
    session = parking_service.get_session_info()
    activity = parking_service.get_activity_log(20)

    session_info = "No active session"
    if session:
        session_info = f"""Session started: {session['start_time']}
  Uptime: {session['uptime_display']}
  Plates detected this session: {session['plates_detected']}
  Spaces used this session: {session['spaces_used']}"""

    activity_lines = "\n".join([
        f"  [{e['timestamp']}] {e['event_type']}: {e['description']}"
        for e in activity[:20]
    ]) if activity else "  No activity recorded"

    return f"""SESSION REPORT:
{session_info}

CURRENT STATUS:
- Total spaces: {stats['total']}, Free: {stats['free']}, Occupied: {stats['occupied']}
- Occupancy rate: {stats['occupancy_pct']}%
- Plates detected today: {stats['plates_today']}

RECENT ACTIVITY LOG:
{activity_lines}"""


def _get_system_prompt(mode):
    """Build the full system prompt with injected live data."""
    if mode == "admin":
        context = _build_admin_context()
        return ADMIN_SYSTEM_PROMPT.format(context=context)
    else:
        context = _build_user_context()
        return USER_SYSTEM_PROMPT.format(context=context, rate=config.PARKING_RATE_PER_HOUR)


import re
import random
from models import database as db

def parse_ai_command(message):
    message_upper = message.strip().upper()
    
    # 1. Park commands
    if re.search(r"\bPARK\s+(?:A\s+)?VIP", message_upper):
        plate = f"VIP-{random.randint(100, 999)}"
        parking_service.save_profile(plate, "vip", "VIP Guest", "Self-parked via AI Chat")
        parking_service.record_detection(plate, "DL", 0.98)
        space_id = parking_service.assign_space(plate)
        if space_id:
            return f"🤖 **AI Command Executed**: Registered and assigned VIP vehicle **{plate}** to premium ground spot **{space_id}**.", {"event": "entry", "space_id": space_id, "plate_text": plate, "profile_type": "vip"}
        else:
            return "🤖 **AI Command Failed**: Sorry, no parking spaces are currently available.", None

    if re.search(r"\bPARK\s+(?:A\s+)?(?:NORMAL\s+)?CAR", message_upper) or re.search(r"\bPARK\s+(?:A\s+)?VEHICLE", message_upper):
        states = ['MH', 'DL', 'KA', 'GJ', 'HR', 'UP']
        letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        plate = f"{random.choice(states)}{random.randint(1, 99):02d}{random.choice(letters)}{random.choice(letters)}{random.randint(1000, 9999):04d}"
        parking_service.record_detection(plate, "MH", 0.95)
        space_id = parking_service.assign_space(plate)
        if space_id:
            return f"🤖 **AI Command Executed**: Parked normal vehicle **{plate}** in space **{space_id}**.", {"event": "entry", "space_id": space_id, "plate_text": plate, "profile_type": "normal"}
        else:
            return "🤖 **AI Command Failed**: Sorry, no parking spaces are currently available.", None

    match_park = re.search(r"\b(?:PARK|ASSIGN|ENTRY)\s+([A-Z0-9-]{4,10})", message_upper)
    if match_park:
        plate = match_park.group(1)
        profile = db.fetchone("SELECT profile_type FROM vehicle_profiles WHERE plate_text = ?", (plate,))
        profile_type = profile["profile_type"] if profile else "normal"
        parking_service.record_detection(plate, "DL", 0.95)
        space_id = parking_service.assign_space(plate)
        if space_id:
            return f"🤖 **AI Command Executed**: Parked vehicle **{plate}** ({profile_type.upper()}) in space **{space_id}**.", {"event": "entry", "space_id": space_id, "plate_text": plate, "profile_type": profile_type}
        else:
            return f"🤖 **AI Command Failed**: No parking spaces available for vehicle **{plate}**.", None

    # 2. Unpark / Release commands
    match_release_space = re.search(r"\b(?:RELEASE|UNPARK|EXIT|FREE)\s+(?:SPACE\s+)?([G|F1|F2]-\d{2})", message_upper)
    if match_release_space:
        space_id = match_release_space.group(1)
        row = db.fetchone("SELECT plate_text FROM parking_spaces WHERE space_id = ? AND is_occupied = 1", (space_id,))
        if not row:
            return f"🤖 **AI Command Alert**: Space **{space_id}** is already free.", None
        
        plate = row["plate_text"]
        result = parking_service.release_space(space_id)
        if result:
            return f"🤖 **AI Command Executed**: Released vehicle **{plate}** from space **{space_id}**.", {"event": "exit", **result}
        else:
            return f"🤖 **AI Command Failed**: Could not release space **{space_id}**.", None

    match_release_plate = re.search(r"\b(?:RELEASE|UNPARK|EXIT|FREE)\s+([A-Z0-9-]{4,10})", message_upper)
    if match_release_plate:
        plate = match_release_plate.group(1)
        row = db.fetchone("SELECT space_id FROM parking_spaces WHERE plate_text = ? AND is_occupied = 1", (plate,))
        if not row:
            return f"🤖 **AI Command Alert**: Vehicle **{plate}** is not currently parked.", None
        
        space_id = row["space_id"]
        result = parking_service.release_space(space_id)
        if result:
            return f"🤖 **AI Command Executed**: Released vehicle **{plate}** from space **{space_id}**.", {"event": "exit", **result}
        else:
            return f"🤖 **AI Command Failed**: Could not release vehicle **{plate}**.", None

    # 3. Security profile management commands
    match_blacklist = re.search(r"\bBLACKLIST\s+([A-Z0-9-]{4,10})", message_upper)
    if match_blacklist:
        plate = match_blacklist.group(1)
        parking_service.save_profile(plate, "blacklist", "Suspect", "Flagged via AI chat command")
        return f"🤖 **AI Command Executed**: Added plate **{plate}** to the SECURITY BLACKLIST BOLO database.", {"event": "profile_change", "plate_text": plate, "profile_type": "blacklist"}

    match_vip_cmd = re.search(r"\bVIP\s+([A-Z0-9-]{4,10})", message_upper)
    if match_vip_cmd:
        plate = match_vip_cmd.group(1)
        parking_service.save_profile(plate, "vip", "VIP Member", "Added via AI chat command")
        return f"🤖 **AI Command Executed**: Registered plate **{plate}** as a VIP Guest profile.", {"event": "profile_change", "plate_text": plate, "profile_type": "vip"}

    match_delete = re.search(r"\b(?:REMOVE|DELETE|CLEAR)\s+(?:VIP|BLACKLIST|PROFILE\s+)?([A-Z0-9-]{4,10})", message_upper)
    if match_delete:
        plate = match_delete.group(1)
        profile = db.fetchone("SELECT profile_type FROM vehicle_profiles WHERE plate_text = ?", (plate,))
        if not profile:
            return f"🤖 **AI Command Alert**: No registered profile found for plate **{plate}**.", None
        
        parking_service.delete_profile(plate)
        return f"🤖 **AI Command Executed**: Removed profile registration for plate **{plate}**.", {"event": "profile_change", "plate_text": plate, "profile_type": "normal"}

    return None, None


def chat_stream(user_message, mode="user", history=None):
    """
    Send a message to Ollama and yield response chunks (streaming).
    history: list of {"role": "user"|"assistant", "content": "..."} dicts
    """
    # Check for direct AI Command-Control patterns
    command_response, action_data = parse_ai_command(user_message)
    if command_response:
        yield {"content": command_response, "action": action_data}
        return

    system_prompt = _get_system_prompt(mode)

    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (last 6 exchanges)
    if history:
        for msg in history[-12:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    try:
        response = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": config.OLLAMA_MODEL,
                "messages": messages,
                "stream": True,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 300,
                }
            },
            stream=True,
            timeout=30,
        )
        response.raise_for_status()

        for line in response.iter_lines():
            if line:
                try:
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

    except requests.exceptions.ConnectionError:
        yield "⚠️ Cannot connect to Ollama. Please make sure it's running (`ollama serve`)."
    except requests.exceptions.Timeout:
        yield "⚠️ Ollama took too long to respond. Try again."
    except Exception as e:
        yield f"⚠️ Error: {str(e)}"


def chat_sync(user_message, mode="user", history=None):
    """Non-streaming chat — returns full response string."""
    chunks = list(chat_stream(user_message, mode, history))
    return "".join(chunks)
