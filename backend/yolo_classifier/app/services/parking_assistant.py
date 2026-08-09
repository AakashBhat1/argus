"""ParkBot — dual-persona Ollama assistant with validated command layer.

Read persona: any active user, tenant-scoped queries only.
Command persona: admin only; LLM proposes structured JSON; code validates + executes.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services import parking_service
from app.services.parking_commands import ALLOWED_ACTIONS, validate_command

logger = logging.getLogger(__name__)

READ_SYSTEM = """You are ParkBot, a helpful parking assistant for Argus.
Answer ONLY from the live tenant data below. Keep answers to 2-4 sentences.
Do not invent numbers. Do not propose database mutations.
Rate: ₹{rate}/hour.

LIVE DATA:
{context}
"""

COMMAND_SYSTEM = """You are ParkBot Admin for Argus parking operations.
You may answer questions OR propose exactly one structured command as JSON
on its own line when the admin requests a mutation.

Allowed actions only: {actions}

Command format (single line JSON, no markdown):
{{"action":"release_space","space_id":"G-01"}}
{{"action":"assign_space","plate_text":"MH12AB1234"}}

If the user is only asking a question, answer in plain text with no JSON.
Never invent SQL or shell commands.

LIVE DATA:
{context}
"""


class ParkingAssistant:
    """Async singleton Ollama client for ParkBot."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    def _settings(self):
        return get_settings()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            s = self._settings()
            self._client = httpx.AsyncClient(
                base_url=s.PARKING_OLLAMA_BASE_URL.rstrip("/"),
                timeout=s.PARKING_OLLAMA_TIMEOUT_SECONDS,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _build_context(self, db: AsyncSession, tenant_id: str) -> str:
        stats = await parking_service.get_stats(db, tenant_id)
        spaces = await parking_service.list_spaces(db, tenant_id)
        free = [s.space_id for s in spaces if not s.is_occupied][:8]
        plates = await parking_service.list_plates(db, tenant_id, limit=5)
        activity = await parking_service.list_activity(db, tenant_id, limit=10)
        recent = ", ".join(f"{p.plate_text}@{p.timestamp}" for p in plates) or "none"
        acts = "; ".join(f"{a.event_type}:{a.description[:60]}" for a in activity) or "none"
        return (
            f"total={stats['total']} free={stats['free']} occupied={stats['occupied']} "
            f"occupancy={stats['occupancy_pct']}% plates_today={stats['plates_today']}\n"
            f"free_spaces={', '.join(free) or 'none'}\n"
            f"recent_plates={recent}\n"
            f"activity={acts}"
        )

    async def _ollama_chat(self, system: str, user_message: str) -> str:
        s = self._settings()
        client = await self._get_client()
        try:
            resp = await client.post(
                "/api/chat",
                json={
                    "model": s.PARKING_OLLAMA_MODEL,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_message},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("message", {}).get("content") or data.get("response") or "")
        except Exception as exc:
            logger.warning("Ollama chat failed: %s", exc)
            return (
                "ParkBot is temporarily unavailable (Ollama unreachable). "
                f"Live context still applies. Error: {exc}"
            )

    @staticmethod
    def _extract_action(text: str) -> Optional[dict[str, Any]]:
        # Prefer fenced or raw JSON objects with "action"
        candidates = re.findall(r"\{[^{}]*\"action\"[^{}]*\}", text, flags=re.DOTALL)
        for raw in candidates:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict) and "action" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
        return None

    async def chat_read(
        self,
        db: AsyncSession,
        tenant_id: str,
        message: str,
    ) -> dict[str, Any]:
        ctx = await self._build_context(db, tenant_id)
        s = self._settings()
        system = READ_SYSTEM.format(rate=s.PARKING_RATE_PER_HOUR, context=ctx)
        reply = await self._ollama_chat(system, message)
        return {"role": "assistant", "mode": "read", "content": reply, "command": None}

    async def chat_command(
        self,
        db: AsyncSession,
        tenant_id: str,
        message: str,
        *,
        actor_user_id: str,
    ) -> dict[str, Any]:
        """Admin path: answer and/or validate+execute structured action."""
        ctx = await self._build_context(db, tenant_id)
        s = self._settings()
        system = COMMAND_SYSTEM.format(
            actions=", ".join(sorted(ALLOWED_ACTIONS)),
            context=ctx,
        )
        reply = await self._ollama_chat(system, message)
        action = self._extract_action(reply)

        # Also allow explicit JSON-only user messages (bypass LLM)
        if action is None:
            try:
                maybe = json.loads(message.strip())
                if isinstance(maybe, dict) and "action" in maybe:
                    action = maybe
            except json.JSONDecodeError:
                pass

        if action is None:
            return {
                "role": "assistant",
                "mode": "command",
                "content": reply,
                "command": None,
                "executed": False,
            }

        ok, err, normalized = validate_command(action)
        if not ok or normalized is None:
            await parking_service.log_activity(
                db,
                tenant_id,
                "command_rejected",
                f"Rejected ParkBot action: {err} payload={action}",
                actor_user_id=actor_user_id,
            )
            return {
                "role": "assistant",
                "mode": "command",
                "content": f"Command rejected: {err}",
                "command": action,
                "executed": False,
                "error": err,
            }

        result = await self._execute(db, tenant_id, normalized, actor_user_id=actor_user_id)
        await parking_service.log_activity(
            db,
            tenant_id,
            "command",
            f"ParkBot executed {normalized['action']}: {result}",
            plate_text=normalized.get("plate_text"),
            space_id=normalized.get("space_id"),
            actor_user_id=actor_user_id,
        )
        return {
            "role": "assistant",
            "mode": "command",
            "content": reply,
            "command": normalized,
            "executed": True,
            "result": result,
        }

    async def _execute(
        self,
        db: AsyncSession,
        tenant_id: str,
        cmd: dict[str, Any],
        *,
        actor_user_id: str,
    ) -> dict[str, Any]:
        action = cmd["action"]
        if action == "release_space":
            out = await parking_service.release_space(
                db,
                tenant_id,
                cmd["space_id"],
                actor_user_id=actor_user_id,
            )
            if out is None:
                return {"ok": False, "error": "Space not found or already free"}
            return {"ok": True, **out}

        if action == "assign_space":
            out = await parking_service.assign_space(db, tenant_id, cmd["plate_text"])
            if out is None:
                return {"ok": False, "error": "No free spaces"}
            return {"ok": True, **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in out.items()}}

        return {"ok": False, "error": f"Unhandled action {action}"}


parking_assistant = ParkingAssistant()
