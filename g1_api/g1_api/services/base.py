"""Base service: holds the core and offers a ``guard`` helper for state changes."""

from __future__ import annotations

from typing import Any, Optional

from g1_api.core.core import RobotCore
from g1_api.core.safety import CommandClass, SafetyContext, SafetyDecision


class BaseService(object):
    def __init__(self, core: RobotCore) -> None:
        self._core = core

    @property
    def core(self) -> RobotCore:
        return self._core

    async def safety_context(self, **overrides: Any) -> SafetyContext:
        return await self._core.safety_context(**overrides)

    async def guard(
        self,
        command_class: CommandClass,
        description: str,
        caller: Optional[str] = None,
        **overrides: Any,
    ) -> SafetyDecision:
        """Check the gate and raise on denial; return the decision on allow."""
        ctx = await self.safety_context(caller=caller, description=description, **overrides)
        decision = self._core.safety_gate.check(command_class, ctx)
        if not decision.allowed:
            raise decision.to_error()
        return decision
