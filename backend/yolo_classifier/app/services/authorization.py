"""Site-level authorization state shared by every camera pipeline.

This answers the question the raw ROI test cannot: *is this person supposed
to be here?* Signals are combined from:

* **Arm state** — armed / disarmed / auto (per-zone schedules decide).
* **Manual grants** — an operator expects a visitor for N minutes, or marks a
  specific live track as known.
* **Vehicle plates** — the gate OCR recognised a plate whose profile is
  authorized (or blacklisted). Persons that step out of that vehicle inherit
  the decision.
* **Identity providers** — pluggable ``IdentityProvider`` hook so that face
  recognition can be added later without touching the risk engine.

The registry is process-local. In a multi-node deployment the same interface
should be backed by Redis so that a plate authorised on the gate machine
unlocks the person seen on the perimeter machine.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional, Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)

ARM_MODES = ("armed", "disarmed", "auto")


@dataclass
class Grant:
    grant_id: str
    tenant_id: str
    kind: str  # manual | plate | face | track
    scope: str  # site | camera | track | vehicle_track
    label: str
    granted_at: float
    expires_at: float
    camera_id: Optional[str] = None
    track_id: Optional[int] = None
    subject: Optional[str] = None  # plate text, person name, ...
    note: Optional[str] = None
    threat: bool = False  # negative grant (blacklisted plate)

    def is_active(self, now: float) -> bool:
        return now < self.expires_at

    def to_dict(self) -> dict:
        data = asdict(self)
        data["seconds_remaining"] = max(0, int(self.expires_at - time.time()))
        return data


@dataclass
class AuthDecision:
    authorized: bool = False
    threat: bool = False
    source: Optional[str] = None
    label: Optional[str] = None
    grant_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class IdentityProvider(Protocol):
    """Hook for identity sources (face recognition, badge readers, ...)."""

    def identify(self, tenant_id: str, camera_id: str, track_id: int) -> Optional[AuthDecision]:
        ...


@dataclass
class _TenantState:
    arm_mode: str = "auto"
    grants: dict[str, Grant] = field(default_factory=dict)
    # vehicle plate seen per (camera_id, track_id)
    vehicle_plates: dict[tuple[str, int], tuple[str, float]] = field(default_factory=dict)


class AuthorizationRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenants: dict[str, _TenantState] = {}
        self._providers: list[IdentityProvider] = []
        self._listeners: list = []

    # -- wiring -------------------------------------------------------------

    def register_identity_provider(self, provider: IdentityProvider) -> None:
        self._providers.append(provider)

    def add_listener(self, callback) -> None:
        """callback(tenant_id: str, event: dict) — invoked on state changes."""
        self._listeners.append(callback)

    def _emit(self, tenant_id: str, event: dict) -> None:
        for callback in list(self._listeners):
            try:
                callback(tenant_id, event)
            except Exception:  # pragma: no cover - listener bugs must not break pipeline
                logger.exception("Authorization listener failed")

    def _state(self, tenant_id: str) -> _TenantState:
        state = self._tenants.get(tenant_id)
        if state is None:
            state = _TenantState()
            self._tenants[tenant_id] = state
        return state

    # -- arm state ----------------------------------------------------------

    def arm_mode(self, tenant_id: str) -> str:
        with self._lock:
            return self._state(tenant_id).arm_mode

    def set_arm_mode(self, tenant_id: str, mode: str, actor: Optional[str] = None) -> str:
        mode = str(mode).lower()
        if mode not in ARM_MODES:
            raise ValueError(f"arm mode must be one of {ARM_MODES}")
        with self._lock:
            self._state(tenant_id).arm_mode = mode
        self._emit(tenant_id, {"event": "arm_mode", "mode": mode, "actor": actor})
        return mode

    # -- grants -------------------------------------------------------------

    def grant_site(
        self,
        tenant_id: str,
        minutes: Optional[float] = None,
        label: str = "Expected visitor",
        note: Optional[str] = None,
    ) -> Grant:
        settings = get_settings()
        duration = float(minutes if minutes is not None else settings.RISK_MANUAL_GRANT_MINUTES) * 60.0
        return self._add_grant(
            tenant_id,
            kind="manual",
            scope="site",
            label=label,
            ttl_sec=duration,
            note=note,
        )

    def grant_track(
        self,
        tenant_id: str,
        camera_id: str,
        track_id: int,
        label: str = "Known person",
        minutes: Optional[float] = None,
        note: Optional[str] = None,
    ) -> Grant:
        settings = get_settings()
        duration = float(minutes if minutes is not None else settings.RISK_MANUAL_GRANT_MINUTES) * 60.0
        return self._add_grant(
            tenant_id,
            kind="track",
            scope="track",
            label=label,
            ttl_sec=duration,
            camera_id=str(camera_id),
            track_id=int(track_id),
            note=note,
        )

    def register_vehicle_plate(
        self,
        tenant_id: str,
        camera_id: str,
        track_id: int,
        plate_text: str,
        profile_type: Optional[str],
        owner_name: Optional[str] = None,
    ) -> Optional[Grant]:
        """Record a plate read on a vehicle track and derive authorization."""
        settings = get_settings()
        ttl = float(settings.RISK_VEHICLE_LINK_TTL_SEC)
        profile = (profile_type or "").lower()
        authorized_types = {p.lower() for p in settings.RISK_AUTHORIZED_PROFILE_TYPES}
        now = time.time()
        with self._lock:
            self._state(tenant_id).vehicle_plates[(str(camera_id), int(track_id))] = (plate_text, now)

        if profile == "blacklist":
            return self._add_grant(
                tenant_id,
                kind="plate",
                scope="site",
                label=f"Blacklisted vehicle {plate_text}",
                ttl_sec=ttl,
                subject=plate_text,
                camera_id=str(camera_id),
                track_id=int(track_id),
                threat=True,
            )
        if profile in authorized_types:
            label = f"{owner_name or profile.title()} vehicle {plate_text}"
            return self._add_grant(
                tenant_id,
                kind="plate",
                scope="site",
                label=label,
                ttl_sec=ttl,
                subject=plate_text,
                camera_id=str(camera_id),
                track_id=int(track_id),
            )
        return None

    def revoke(self, tenant_id: str, grant_id: str) -> bool:
        with self._lock:
            removed = self._state(tenant_id).grants.pop(grant_id, None)
        if removed:
            self._emit(tenant_id, {"event": "grant_revoked", "grant": removed.to_dict()})
        return removed is not None

    def list_grants(self, tenant_id: str) -> list[dict]:
        now = time.time()
        with self._lock:
            state = self._state(tenant_id)
            self._purge(state, now)
            return [g.to_dict() for g in sorted(state.grants.values(), key=lambda g: g.granted_at, reverse=True)]

    def _add_grant(
        self,
        tenant_id: str,
        *,
        kind: str,
        scope: str,
        label: str,
        ttl_sec: float,
        camera_id: Optional[str] = None,
        track_id: Optional[int] = None,
        subject: Optional[str] = None,
        note: Optional[str] = None,
        threat: bool = False,
    ) -> Grant:
        now = time.time()
        grant = Grant(
            grant_id=uuid.uuid4().hex[:12],
            tenant_id=tenant_id,
            kind=kind,
            scope=scope,
            label=label,
            granted_at=now,
            expires_at=now + max(1.0, ttl_sec),
            camera_id=camera_id,
            track_id=track_id,
            subject=subject,
            note=note,
            threat=threat,
        )
        with self._lock:
            state = self._state(tenant_id)
            self._purge(state, now)
            state.grants[grant.grant_id] = grant
        self._emit(tenant_id, {"event": "grant_added", "grant": grant.to_dict()})
        return grant

    @staticmethod
    def _purge(state: _TenantState, now: float) -> None:
        for grant_id in [gid for gid, g in state.grants.items() if not g.is_active(now)]:
            del state.grants[grant_id]
        stale = [k for k, (_, ts) in state.vehicle_plates.items() if now - ts > 3600]
        for key in stale:
            del state.vehicle_plates[key]

    # -- resolution ---------------------------------------------------------

    def vehicle_plate(self, tenant_id: str, camera_id: str, track_id: int) -> Optional[str]:
        with self._lock:
            entry = self._state(tenant_id).vehicle_plates.get((str(camera_id), int(track_id)))
        return entry[0] if entry else None

    def resolve(
        self,
        tenant_id: str,
        camera_id: str,
        track_id: int,
        linked_vehicle_track: Optional[int] = None,
        spawned_from_vehicle: bool = False,
    ) -> AuthDecision:
        """Decide whether a person track is authorized right now.

        Priority: threat flags > explicit track grant > vehicle-derived grant >
        site-wide manual grant > identity providers.
        """
        now = time.time()
        with self._lock:
            state = self._state(tenant_id)
            self._purge(state, now)
            grants = list(state.grants.values())

        # Threat first: a blacklisted vehicle taints the persons linked to it.
        for grant in grants:
            if grant.threat and self._vehicle_matches(grant, camera_id, linked_vehicle_track):
                return AuthDecision(False, True, "plate", grant.label, grant.grant_id)

        for grant in grants:
            if grant.threat:
                continue
            if grant.scope == "track" and grant.camera_id == str(camera_id) and grant.track_id == int(track_id):
                return AuthDecision(True, False, grant.kind, grant.label, grant.grant_id)

        if linked_vehicle_track is not None:
            for grant in grants:
                if grant.threat or grant.kind != "plate":
                    continue
                if self._vehicle_matches(grant, camera_id, linked_vehicle_track):
                    return AuthDecision(True, False, "plate", grant.label, grant.grant_id)

        if spawned_from_vehicle:
            # An authorized vehicle arrived on site recently (any camera): the
            # person stepping out of *a* vehicle is most likely its occupant.
            for grant in grants:
                if grant.kind == "plate" and not grant.threat and grant.scope == "site":
                    return AuthDecision(True, False, "plate_site", grant.label, grant.grant_id)

        for grant in grants:
            if grant.kind == "manual" and grant.scope == "site" and not grant.threat:
                return AuthDecision(True, False, "manual", grant.label, grant.grant_id)

        for provider in self._providers:
            try:
                decision = provider.identify(tenant_id, str(camera_id), int(track_id))
            except Exception:  # pragma: no cover
                logger.exception("Identity provider failed")
                decision = None
            if decision is not None:
                return decision

        return AuthDecision()

    @staticmethod
    def _vehicle_matches(grant: Grant, camera_id: str, vehicle_track: Optional[int]) -> bool:
        if vehicle_track is None:
            return False
        return grant.camera_id == str(camera_id) and grant.track_id == int(vehicle_track)

    # -- snapshot -------------------------------------------------------------

    def snapshot(self, tenant_id: str) -> dict:
        return {
            "arm_mode": self.arm_mode(tenant_id),
            "grants": self.list_grants(tenant_id),
        }


authorization_registry = AuthorizationRegistry()
