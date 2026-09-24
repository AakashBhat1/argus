"""Site-level state parking learns from surveillance (arming)."""

from __future__ import annotations

import threading

ARM_MODES = ("armed", "disarmed", "auto")


class SiteState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._arm_mode: dict[str, str] = {}

    def arm_mode(self, tenant_id: str) -> str:
        with self._lock:
            return self._arm_mode.get(tenant_id, "auto")

    def set_arm_mode(self, tenant_id: str, mode: str) -> None:
        if mode not in ARM_MODES:
            raise ValueError(f"arm mode must be one of {ARM_MODES}")
        with self._lock:
            self._arm_mode[tenant_id] = mode


site_state = SiteState()
