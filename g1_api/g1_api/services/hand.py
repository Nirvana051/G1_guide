"""Hand service: dexterous hand TCP commands."""

from __future__ import annotations

from typing import Optional

from g1_api.core.safety import CommandClass
from g1_api.services.base import BaseService


class HandService(BaseService):
    async def send_command(self, cmd: str, caller: Optional[str] = None) -> str:
        await self.guard(CommandClass.HAND, "hand command %s" % cmd, caller=caller)
        return await self._core.adapter.hand.send_command(cmd)
