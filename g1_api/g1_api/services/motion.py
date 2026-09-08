"""Motion service: submit, query and cancel actions through the single slot."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from g1_api.errors import ValidationError
from g1_api.models.action import (
    KIND_BY_ACTION_NAME,
    ActionKind,
    ActionRecord,
    ActionRequest,
)
from g1_api.services.base import BaseService


class MotionService(BaseService):
    async def submit(
        self, action_name: str, options: Optional[Dict[str, Any]], requester: Optional[str] = None
    ) -> ActionRecord:
        kind = KIND_BY_ACTION_NAME.get(str(action_name))
        if kind is None:
            raise ValidationError(
                "unknown action_name %r" % (action_name,),
                field="action_name",
                expected="one of %s" % ", ".join(sorted(KIND_BY_ACTION_NAME)),
                source="services.motion",
            )
        # Validate the raw wire options against the factory schema before
        # materialising typed parameters.
        self._core.registry.validate_params(kind, options or {})
        request = ActionRequest.from_dict({"action_name": action_name, "options": options or {}})
        if ActionKind(request.kind) is ActionKind.MOVE:
            self._validate_move(request.params)
        return await self._core.task_manager.submit(request, requester=requester)

    def _validate_move(self, params: Any) -> None:
        s = self._core.config.safety
        if int(params.duration_ms) > int(s.max_move_duration_ms):
            raise ValidationError(
                "MoveAction duration_ms exceeds the configured ceiling %d ms"
                % (s.max_move_duration_ms,),
                field="options.duration_ms",
                source="services.motion",
            )
        vx = abs(float(params.vx_mps))
        vy = abs(float(params.vy_mps))
        omega = abs(float(params.omega_radps))
        if vx < s.deadzone_vx and vy < s.deadzone_vy and omega < s.deadzone_omega:
            raise ValidationError(
                "MoveAction velocity is below the deadzone (vx/vy %.1f, omega %.1f): "
                "the command would produce no motion" % (s.deadzone_vx, s.deadzone_omega),
                field="options",
                source="services.motion",
            )

    def get(self, action_id: str) -> ActionRecord:
        return self._core.task_manager.get(action_id)

    def get_by_compat_id(self, compat_id: int) -> ActionRecord:
        return self._core.task_manager.get_by_compat_id(compat_id)

    def current(self) -> Optional[ActionRecord]:
        return self._core.task_manager.current()

    def list(self, limit: Optional[int] = None) -> Tuple[ActionRecord, ...]:
        return self._core.task_manager.list(limit)

    async def cancel(self, action_id: str) -> ActionRecord:
        return await self._core.task_manager.cancel(action_id)

    async def cancel_current(self) -> ActionRecord:
        return await self._core.task_manager.cancel_current()

    def list_factories(self, include_schema: bool = True) -> List[Dict[str, Any]]:
        return self._core.registry.list_factories(include_schema)
