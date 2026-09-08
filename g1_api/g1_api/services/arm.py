"""Arm service: list actions, execute one (with automatic release + settle wait)."""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from g1_api.core.safety import CommandClass
from g1_api.services.base import BaseService


class ArmService(BaseService):
    async def get_action_list(self) -> Dict[int, str]:
        return await self._core.adapter.arm.get_action_list()

    async def execute_action(self, action_id: int, caller: Optional[str] = None) -> Dict[str, int]:
        await self.guard(CommandClass.ARM, "execute arm action %d" % action_id, caller=caller)
        await self._core.adapter.arm.execute_action(action_id)
        # The reference implementation always releases the arm (action 99) and
        # waits 3 s afterwards, so the arm settles.
        release_id = self._core.config.tour.arm_release_action_id
        await self._core.adapter.arm.execute_action(release_id)
        await asyncio.sleep(self._core.config.tour.arm_release_wait_s)
        return {"action_id": action_id, "release_action_id": release_id}
