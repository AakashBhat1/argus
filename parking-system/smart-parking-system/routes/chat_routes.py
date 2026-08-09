"""
Smart Parking System — AI Chat Routes (SSE Streaming)
"""
from flask import Blueprint, request, Response, stream_with_context, jsonify
import json
from services import ai_assistant

chat_bp = Blueprint("chat", __name__, url_prefix="/api/chat")


@chat_bp.route("", methods=["POST"])
def chat():
    """
    Send a chat message. Returns SSE stream of response chunks.
    Body: {"message": "...", "mode": "user"|"admin", "history": [...]}
    """
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    mode = data.get("mode", "user")
    history = data.get("history", [])

    if not message:
        return jsonify({"error": "No message provided"}), 400

    if mode not in ("user", "admin"):
        mode = "user"

    def generate():
        for chunk in ai_assistant.chat_stream(message, mode, history):
            # SSE format
            if isinstance(chunk, dict):
                yield f"data: {json.dumps(chunk)}\n\n"
            else:
                yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
