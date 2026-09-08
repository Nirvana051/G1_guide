"""Canonical enumerations, all **open** (unknown values resolve to UNKNOWN)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Tuple

__all__ = [
    "OpenEnum",
    "ActionKind",
    "ActionLifecycle",
    "ActionResult",
    "ActionStage",
    "LocalizationState",
    "TERMINAL_LIFECYCLES",
    "ACTIVE_LIFECYCLES",
]


class OpenEnum(str, Enum):
    """A string enum that never raises: unknown values resolve to ``UNKNOWN``."""

    @classmethod
    def _missing_(cls, value: Any) -> Any:
        if isinstance(value, str):
            wanted = value.strip().lower()
            for member in cls:
                if member.value.lower() == wanted or member.name.lower() == wanted:
                    return member
        return cls.__members__.get("UNKNOWN")

    def __str__(self) -> str:
        return str(self.value)

    @property
    def is_unknown(self) -> bool:
        return self.name == "UNKNOWN"

    @classmethod
    def parse(cls, value: Any, default: Optional["OpenEnum"] = None) -> Any:
        if value is None or value == "":
            return default if default is not None else cls.__members__.get("UNKNOWN")
        return cls(value)


class ActionKind(OpenEnum):
    """The four action factories the G1 API implements."""

    MOVE = "move"
    MOVE_TO = "move_to"
    ROTATE = "rotate"
    ROTATE_TO = "rotate_to"
    UNKNOWN = "unknown"


class ActionLifecycle(OpenEnum):
    """Named action states, replacing the reference's magic integers.

    ``PENDING (0) / RUNNING (1) / PAUSED (3) / terminal (4)``; value ``2`` is
    absent from the reference enum entirely.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_LIFECYCLES

    @property
    def is_active(self) -> bool:
        return self in ACTIVE_LIFECYCLES


TERMINAL_LIFECYCLES: FrozenSet[ActionLifecycle] = frozenset(
    {ActionLifecycle.SUCCEEDED, ActionLifecycle.FAILED, ActionLifecycle.CANCELLED}
)
ACTIVE_LIFECYCLES: FrozenSet[ActionLifecycle] = frozenset(
    {ActionLifecycle.PENDING, ActionLifecycle.RUNNING, ActionLifecycle.PAUSED}
)


class ActionResult(OpenEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class ActionStage(OpenEnum):
    """Fine-grained sub-state. ``stage`` on the wire is free text, but we also
    keep a closed vocabulary for the common cases."""

    IDLE = "IDLE"
    GOING_TO_TARGET = "GOING_TO_TARGET"
    ROTATING = "ROTATING"
    MOVING = "MOVING"
    DONE = "DONE"
    UNKNOWN = "UNKNOWN"


class LocalizationState(OpenEnum):
    INITIALIZED = "INITIALIZED"
    NOT_INITIALIZED = "NOT_INITIALIZED"
    RELOCALIZING = "RELOCALIZING"
    UNKNOWN = "UNKNOWN"


#: Reference (status, result) integer pair per lifecycle. ``result`` is only
#: meaningful once ``status == 4``.
COMPAT_STATUS_RESULT: Dict[ActionLifecycle, Tuple[int, int]] = {
    ActionLifecycle.PENDING: (0, 0),
    ActionLifecycle.RUNNING: (1, 0),
    ActionLifecycle.PAUSED: (3, 0),
    ActionLifecycle.SUCCEEDED: (4, 0),
    ActionLifecycle.FAILED: (4, -1),
    ActionLifecycle.CANCELLED: (4, -2),
    ActionLifecycle.UNKNOWN: (4, -1),
}
