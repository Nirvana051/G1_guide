"""System service: identity, capabilities, health."""

from __future__ import annotations

from typing import Tuple

from g1_api.models.state import CapabilityInfo, DeviceIdentity, HealthState
from g1_api.services.base import BaseService


class SystemService(BaseService):
    async def get_info(self) -> DeviceIdentity:
        return await self._core.adapter.system.get_identity()

    async def get_capabilities(self) -> Tuple[CapabilityInfo, ...]:
        return await self._core.adapter.system.get_capabilities()

    async def get_health(self) -> HealthState:
        return await self._core.adapter.system.get_health()
