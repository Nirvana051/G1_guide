"""Localization service: pose, relocalize, status."""

from __future__ import annotations

from typing import Optional

from g1_api.core.safety import CommandClass
from g1_api.errors import SafetyInterlockError
from g1_api.models.geometry import Pose2D
from g1_api.models.state import LocalizationStatus
from g1_api.services.base import BaseService


class LocalizationService(BaseService):
    async def get_pose(self) -> Pose2D:
        return await self._core.adapter.localization.get_pose()

    async def get_status(self) -> LocalizationStatus:
        return await self._core.adapter.localization.get_status()

    async def relocalize(
        self, x: float, y: float, yaw: float, caller: Optional[str] = None
    ) -> LocalizationStatus:
        # Relocalization requires a selected map (its map.pcd is the reference
        # cloud); it must NOT be blocked by the localization_not_initialized
        # interlock, which is exactly what it clears.
        await self.guard(CommandClass.STATE_CHANGE, "relocalize", caller=caller)
        current = await self._core.adapter.map.get_current_map()
        if current is None:
            raise SafetyInterlockError(
                "refused by safety gate: map_not_selected",
                interlocks=["map_not_selected"],
                source="services.localization",
            )
        return await self._core.adapter.localization.relocalize(x, y, yaw)
