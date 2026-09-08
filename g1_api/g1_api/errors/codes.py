"""Error, severity and failure vocabularies.

All enums are **open**: an unrecognised wire value resolves to the type's
``UNKNOWN`` member instead of raising, because hardware ships ahead of its own
spec and a strict validator rejects valid data from its own robot.

:class:`ErrorCode` is the machine-readable code carried in every RFC 9457
problem detail, paired with a fixed HTTP status. :class:`FailureCode` is the
enumerated action outcome -- the fix for the reference API, whose diagnosis is
free text in ``ActionState.reason`` and whose vocabulary is undocumented.
"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "ErrorCategory",
    "ErrorSeverity",
    "ComponentId",
    "ErrorCode",
    "FailureCode",
    "HTTP_STATUS_BY_ERROR_CODE",
    "http_status_for",
]


class _OpenStrEnum(str, Enum):
    """A string enum whose unknown values resolve to ``UNKNOWN`` instead of raising."""

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


class _OpenIntEnum(IntEnum):
    """An int enum whose unknown values resolve to ``UNKNOWN`` instead of raising."""

    @classmethod
    def _missing_(cls, value: Any) -> Any:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return cls.__members__.get("UNKNOWN")
        for member in cls:
            if member.value == number:
                return member
        return cls.__members__.get("UNKNOWN")


class ErrorCategory(_OpenStrEnum):
    VALIDATION = "VALIDATION"
    AUTH = "AUTH"
    PERMISSION = "PERMISSION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    SAFETY = "SAFETY"
    STATE = "STATE"
    CAPABILITY = "CAPABILITY"
    HARDWARE = "HARDWARE"
    ADAPTER = "ADAPTER"
    INTERNAL = "INTERNAL"
    UNKNOWN = "UNKNOWN"


class ErrorSeverity(_OpenIntEnum):
    """Numbering mirrors the reference ``BaseError.level``: 0/1/2/**4**/255."""

    HEALTHY = 0
    WARN = 1
    ERROR = 2
    FATAL = 4
    UNKNOWN = 255

    @property
    def is_actionable(self) -> bool:
        return self in (ErrorSeverity.ERROR, ErrorSeverity.FATAL)


class ComponentId(_OpenIntEnum):
    USER = 0
    SYSTEM = 1
    POWER = 2
    MOTION = 3
    SENSOR = 4
    UNKNOWN = 255


class ErrorCode(_OpenStrEnum):
    """API-level error codes. Each maps to exactly one HTTP status.

    This is the correction to the reference API's overloaded ``400``, where
    "your JSON is malformed" and "the robot will not do this right now" are the
    same status.
    """

    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN_SCOPE = "FORBIDDEN_SCOPE"
    NOT_FOUND = "NOT_FOUND"
    ROBOT_BUSY = "ROBOT_BUSY"
    SAFETY_INTERLOCK = "SAFETY_INTERLOCK"
    MAP_VERSION_STALE = "MAP_VERSION_STALE"
    UNSAFE_REQUEST = "UNSAFE_REQUEST"
    NOT_READY = "NOT_READY"
    CAPABILITY_ABSENT = "CAPABILITY_ABSENT"
    HARDWARE_FAULT = "HARDWARE_FAULT"
    ADAPTER_ERROR = "ADAPTER_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNKNOWN = "UNKNOWN"


class FailureCode(_OpenStrEnum):
    """Why an action ended without succeeding. Clients branch on this, never on
    the free-text ``reason``."""

    GOAL_UNREACHABLE = "GOAL_UNREACHABLE"
    PATH_BLOCKED = "PATH_BLOCKED"
    ROBOT_BLOCKED = "ROBOT_BLOCKED"
    LOCALIZATION_NOT_READY = "LOCALIZATION_NOT_READY"
    LOCALIZATION_LOST = "LOCALIZATION_LOST"
    SAFETY_INTERLOCK = "SAFETY_INTERLOCK"
    TIMEOUT = "TIMEOUT"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    HARDWARE_FAULT = "HARDWARE_FAULT"
    UNKNOWN = "UNKNOWN"


HTTP_STATUS_BY_ERROR_CODE: Dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN_SCOPE: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ROBOT_BUSY: 409,
    ErrorCode.SAFETY_INTERLOCK: 409,
    ErrorCode.MAP_VERSION_STALE: 409,
    ErrorCode.UNSAFE_REQUEST: 422,
    ErrorCode.NOT_READY: 503,
    ErrorCode.CAPABILITY_ABSENT: 501,
    ErrorCode.HARDWARE_FAULT: 500,
    ErrorCode.ADAPTER_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.UNKNOWN: 500,
}


def http_status_for(code: Optional[ErrorCode]) -> int:
    if code is None:
        return 500
    return HTTP_STATUS_BY_ERROR_CODE.get(ErrorCode(code), 500)
