"""The action lifecycle, as an explicit transition table.

``PENDING -> RUNNING -> {PAUSED <-> RUNNING} -> {SUCCEEDED, FAILED, CANCELLED}``.
Terminal states have no outgoing transitions. Illegal transitions raise
``ConflictError``.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

from g1_api.errors import ErrorCode, ConflictError, FailureCode
from g1_api.models.enums import (
    COMPAT_STATUS_RESULT,
    ActionLifecycle,
    ActionResult,
)

__all__ = ["ALLOWED_TRANSITIONS", "ActionStateMachine", "compat_status_result"]

ALLOWED_TRANSITIONS: Mapping[ActionLifecycle, FrozenSet[ActionLifecycle]] = {
    ActionLifecycle.PENDING: frozenset(
        {ActionLifecycle.RUNNING, ActionLifecycle.CANCELLED, ActionLifecycle.FAILED}
    ),
    ActionLifecycle.RUNNING: frozenset(
        {
            ActionLifecycle.PAUSED,
            ActionLifecycle.SUCCEEDED,
            ActionLifecycle.FAILED,
            ActionLifecycle.CANCELLED,
        }
    ),
    ActionLifecycle.PAUSED: frozenset(
        {
            ActionLifecycle.RUNNING,
            ActionLifecycle.SUCCEEDED,
            ActionLifecycle.FAILED,
            ActionLifecycle.CANCELLED,
        }
    ),
    ActionLifecycle.SUCCEEDED: frozenset(),
    ActionLifecycle.FAILED: frozenset(),
    ActionLifecycle.CANCELLED: frozenset(),
    ActionLifecycle.UNKNOWN: frozenset(),
}


def compat_status_result(lifecycle: ActionLifecycle) -> Tuple[int, int]:
    return COMPAT_STATUS_RESULT.get(ActionLifecycle(lifecycle), (4, -1))


class ActionStateMachine(object):
    def __init__(
        self, lifecycle: ActionLifecycle = ActionLifecycle.PENDING, action_id: Optional[str] = None
    ) -> None:
        self._lifecycle = ActionLifecycle(lifecycle)
        self._action_id = action_id
        self._result: Optional[ActionResult] = None
        self._failure_code: Optional[FailureCode] = None
        self._reason: str = ""

    @property
    def lifecycle(self) -> ActionLifecycle:
        return self._lifecycle

    @property
    def action_id(self) -> Optional[str]:
        return self._action_id

    @property
    def result(self) -> Optional[ActionResult]:
        return self._result

    @property
    def failure_code(self) -> Optional[FailureCode]:
        return self._failure_code

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def is_terminal(self) -> bool:
        return self._lifecycle.is_terminal

    @property
    def is_active(self) -> bool:
        return self._lifecycle.is_active

    def can_transition(self, target: ActionLifecycle) -> bool:
        return ActionLifecycle(target) in ALLOWED_TRANSITIONS.get(self._lifecycle, frozenset())

    def transition(
        self,
        target: ActionLifecycle,
        result: Optional[ActionResult] = None,
        failure_code: Optional[FailureCode] = None,
        reason: Optional[str] = None,
    ) -> ActionLifecycle:
        wanted = ActionLifecycle(target)
        if wanted is self._lifecycle:
            return self._lifecycle
        if not self.can_transition(wanted):
            allowed = sorted(str(s) for s in ALLOWED_TRANSITIONS.get(self._lifecycle, frozenset()))
            raise ConflictError(
                "action %s cannot go from %s to %s"
                % (self._action_id or "<unnamed>", self._lifecycle, wanted),
                code=ErrorCode.ROBOT_BUSY,
                source="state.action_state_machine",
                details={
                    "action_id": self._action_id,
                    "current_state": str(self._lifecycle),
                    "requested_state": str(wanted),
                    "allowed_states": allowed,
                },
            )
        self._lifecycle = wanted
        if reason is not None:
            self._reason = str(reason)
        if failure_code is not None:
            self._failure_code = FailureCode(failure_code)
        if wanted.is_terminal:
            self._result = (
                ActionResult(result)
                if result is not None
                else {
                    ActionLifecycle.SUCCEEDED: ActionResult.SUCCESS,
                    ActionLifecycle.FAILED: ActionResult.FAILED,
                    ActionLifecycle.CANCELLED: ActionResult.CANCELLED,
                }.get(wanted, ActionResult.UNKNOWN)
            )
        return self._lifecycle

    def start(self) -> ActionLifecycle:
        return self.transition(ActionLifecycle.RUNNING)

    def pause(self, reason: str = "") -> ActionLifecycle:
        return self.transition(ActionLifecycle.PAUSED, reason=reason or None)

    def resume(self) -> ActionLifecycle:
        return self.transition(ActionLifecycle.RUNNING)

    def succeed(self, reason: str = "") -> ActionLifecycle:
        return self.transition(ActionLifecycle.SUCCEEDED, result=ActionResult.SUCCESS, reason=reason or None)

    def fail(
        self, failure_code: FailureCode = FailureCode.UNKNOWN, reason: str = ""
    ) -> ActionLifecycle:
        return self.transition(
            ActionLifecycle.FAILED,
            result=ActionResult.FAILED,
            failure_code=failure_code,
            reason=reason or None,
        )

    def cancel(self, reason: str = "cancelled by request") -> ActionLifecycle:
        return self.transition(
            ActionLifecycle.CANCELLED,
            result=ActionResult.CANCELLED,
            failure_code=FailureCode.CANCELLED_BY_USER,
            reason=reason or None,
        )

    def to_compat_status_result(self) -> Tuple[int, int]:
        return compat_status_result(self._lifecycle)

    def to_dict(self) -> Dict[str, Any]:
        status, result = self.to_compat_status_result()
        return {
            "action_id": self._action_id,
            "state": str(self._lifecycle),
            "result": None if self._result is None else str(self._result),
            "failure_code": None if self._failure_code is None else str(self._failure_code),
            "reason": self._reason,
            "compat": {"status": status, "result": result},
        }

    def __repr__(self) -> str:
        return "ActionStateMachine(id=%r, state=%s, result=%s)" % (
            self._action_id,
            self._lifecycle,
            self._result,
        )
