"""Errors: the single exception tree plus its vocabularies."""

from __future__ import annotations

from g1_api.errors.codes import (
    ComponentId,
    ErrorCategory,
    ErrorCode,
    ErrorSeverity,
    FailureCode,
    HTTP_STATUS_BY_ERROR_CODE,
    http_status_for,
)
from g1_api.errors.exceptions import (
    PROBLEM_TYPE_BASE,
    AdapterError,
    AuthError,
    CapabilityAbsentError,
    ConflictError,
    HardwareFaultError,
    InternalError,
    MapVersionStaleError,
    NotReadyError,
    NotFoundError,
    RobotBusyError,
    RobotError,
    SafetyInterlockError,
    ScopeError,
    UnsafeRequestError,
    ValidationError,
    to_robot_error,
)

__all__ = [
    "ComponentId",
    "ErrorCategory",
    "ErrorCode",
    "ErrorSeverity",
    "FailureCode",
    "HTTP_STATUS_BY_ERROR_CODE",
    "http_status_for",
    "PROBLEM_TYPE_BASE",
    "AdapterError",
    "AuthError",
    "CapabilityAbsentError",
    "ConflictError",
    "HardwareFaultError",
    "InternalError",
    "MapVersionStaleError",
    "NotReadyError",
    "NotFoundError",
    "RobotBusyError",
    "RobotError",
    "SafetyInterlockError",
    "ScopeError",
    "UnsafeRequestError",
    "ValidationError",
    "to_robot_error",
]
