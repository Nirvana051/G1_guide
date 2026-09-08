"""The exception hierarchy. Every failure is a :class:`RobotError`.

A ``RobotError`` carries everything the gateway needs -- code, category,
severity, HTTP status, machine details -- and renders itself as an RFC 9457
``application/problem+json`` document.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Mapping, Optional

from g1_api.errors.codes import (
    ComponentId,
    ErrorCategory,
    ErrorCode,
    ErrorSeverity,
    FailureCode,
    http_status_for,
)

__all__ = [
    "PROBLEM_TYPE_BASE",
    "RobotError",
    "ValidationError",
    "AuthError",
    "ScopeError",
    "NotFoundError",
    "ConflictError",
    "RobotBusyError",
    "SafetyInterlockError",
    "MapVersionStaleError",
    "UnsafeRequestError",
    "NotReadyError",
    "AdapterError",
    "CapabilityAbsentError",
    "HardwareFaultError",
    "InternalError",
    "to_robot_error",
]

PROBLEM_TYPE_BASE = "https://g1-api.invalid/errors"

_TITLES: Dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "Invalid request",
    ErrorCode.UNAUTHENTICATED: "Authentication required",
    ErrorCode.FORBIDDEN_SCOPE: "Insufficient scope",
    ErrorCode.NOT_FOUND: "Not found",
    ErrorCode.ROBOT_BUSY: "Robot busy",
    ErrorCode.SAFETY_INTERLOCK: "Safety interlock",
    ErrorCode.MAP_VERSION_STALE: "Map version stale",
    ErrorCode.UNSAFE_REQUEST: "Unsafe request",
    ErrorCode.NOT_READY: "Not ready",
    ErrorCode.CAPABILITY_ABSENT: "Capability absent",
    ErrorCode.HARDWARE_FAULT: "Hardware fault",
    ErrorCode.ADAPTER_ERROR: "Adapter error",
    ErrorCode.INTERNAL_ERROR: "Internal error",
    ErrorCode.UNKNOWN: "Error",
}

_CATEGORIES: Dict[ErrorCode, ErrorCategory] = {
    ErrorCode.VALIDATION_ERROR: ErrorCategory.VALIDATION,
    ErrorCode.UNAUTHENTICATED: ErrorCategory.AUTH,
    ErrorCode.FORBIDDEN_SCOPE: ErrorCategory.PERMISSION,
    ErrorCode.NOT_FOUND: ErrorCategory.NOT_FOUND,
    ErrorCode.ROBOT_BUSY: ErrorCategory.CONFLICT,
    ErrorCode.SAFETY_INTERLOCK: ErrorCategory.SAFETY,
    ErrorCode.MAP_VERSION_STALE: ErrorCategory.CONFLICT,
    ErrorCode.UNSAFE_REQUEST: ErrorCategory.SAFETY,
    ErrorCode.NOT_READY: ErrorCategory.STATE,
    ErrorCode.CAPABILITY_ABSENT: ErrorCategory.CAPABILITY,
    ErrorCode.HARDWARE_FAULT: ErrorCategory.HARDWARE,
    ErrorCode.ADAPTER_ERROR: ErrorCategory.ADAPTER,
    ErrorCode.INTERNAL_ERROR: ErrorCategory.INTERNAL,
    ErrorCode.UNKNOWN: ErrorCategory.UNKNOWN,
}

_FAILURE_BY_CODE: Dict[ErrorCode, FailureCode] = {
    ErrorCode.SAFETY_INTERLOCK: FailureCode.SAFETY_INTERLOCK,
    ErrorCode.MAP_VERSION_STALE: FailureCode.PRECONDITION_FAILED,
    ErrorCode.HARDWARE_FAULT: FailureCode.HARDWARE_FAULT,
    ErrorCode.NOT_READY: FailureCode.PRECONDITION_FAILED,
    ErrorCode.UNSAFE_REQUEST: FailureCode.PRECONDITION_FAILED,
}


def _slug(code: ErrorCode) -> str:
    return str(code.value).lower().replace("_", "-")


class RobotError(Exception):
    """Base class for every error this platform reports."""

    default_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    default_severity: ErrorSeverity = ErrorSeverity.ERROR

    def __init__(
        self,
        message: str,
        code: Optional[ErrorCode] = None,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None,
        source: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
        http_status: Optional[int] = None,
        timestamp_utc_ns: Optional[int] = None,
        component: Optional[ComponentId] = None,
        retryable: Optional[bool] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.code = ErrorCode(code) if code is not None else self.default_code
        self.category = (
            ErrorCategory(category)
            if category is not None
            else _CATEGORIES.get(self.code, ErrorCategory.UNKNOWN)
        )
        self.http_status = (
            int(http_status) if http_status is not None else http_status_for(self.code)
        )
        self.severity = (
            ErrorSeverity(severity)
            if severity is not None
            else (self.default_severity if self.http_status >= 500 else ErrorSeverity.WARN)
        )
        self.source = source
        self.details: Dict[str, Any] = dict(details) if details else {}
        self.timestamp_utc_ns = (
            int(timestamp_utc_ns) if timestamp_utc_ns is not None else time.time_ns()
        )
        self.component = (
            ComponentId(component) if component is not None else ComponentId.UNKNOWN
        )
        self.cause = cause
        self._retryable = retryable

    @property
    def retryable(self) -> bool:
        if self._retryable is not None:
            return bool(self._retryable)
        return self.code in (ErrorCode.ROBOT_BUSY, ErrorCode.NOT_READY, ErrorCode.ADAPTER_ERROR)

    @property
    def title(self) -> str:
        return _TITLES.get(self.code, "Error")

    @property
    def problem_type(self) -> str:
        return "%s/%s" % (PROBLEM_TYPE_BASE, _slug(self.code))

    @property
    def failure_code(self) -> Optional[FailureCode]:
        return _FAILURE_BY_CODE.get(self.code)

    def to_problem_detail(
        self, instance: Optional[str] = None, trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        document: Dict[str, Any] = {
            "type": self.problem_type,
            "title": self.title,
            "status": self.http_status,
            "code": str(self.code.value),
            "detail": self.message,
        }
        if instance:
            document["instance"] = instance
        if trace_id:
            document["trace_id"] = trace_id
        document["category"] = str(self.category.value)
        document["severity"] = int(self.severity.value)
        document["retryable"] = self.retryable
        document["timestamp_utc_ns"] = self.timestamp_utc_ns
        if self.source:
            document["source"] = self.source
        if self.details:
            document["details"] = dict(self.details)
        return document

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": str(self.code.value),
            "category": str(self.category.value),
            "severity": int(self.severity.value),
            "status": self.http_status,
            "message": self.message,
            "source": self.source,
            "retryable": self.retryable,
            "timestamp_utc_ns": self.timestamp_utc_ns,
            "details": dict(self.details),
        }

    def with_details(self, **extra: Any) -> "RobotError":
        self.details.update(extra)
        return self

    def __repr__(self) -> str:
        return "%s(code=%s, status=%d, message=%r)" % (
            type(self).__name__,
            self.code.value,
            self.http_status,
            self.message,
        )


class ValidationError(RobotError):
    default_code = ErrorCode.VALIDATION_ERROR
    default_severity = ErrorSeverity.WARN

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        expected: Optional[str] = None,
        got: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if field is not None:
            details.setdefault("field", field)
        if expected is not None:
            details.setdefault("expected", expected)
        if got is not None:
            details.setdefault("got", repr(got))
        kwargs.setdefault("code", ErrorCode.VALIDATION_ERROR)
        super().__init__(message, details=details, **kwargs)


class AuthError(RobotError):
    default_code = ErrorCode.UNAUTHENTICATED
    default_severity = ErrorSeverity.WARN

    def __init__(self, message: str = "Authentication required", **kwargs: Any) -> None:
        kwargs.setdefault("code", ErrorCode.UNAUTHENTICATED)
        super().__init__(message, **kwargs)


class ScopeError(RobotError):
    default_code = ErrorCode.FORBIDDEN_SCOPE
    default_severity = ErrorSeverity.WARN

    def __init__(
        self,
        message: str = "Insufficient scope",
        required: Optional[str] = None,
        granted: Optional[Iterable[str]] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if required is not None:
            details.setdefault("required_scope", required)
        if granted is not None:
            details.setdefault("granted_scopes", sorted(str(s) for s in granted))
        kwargs.setdefault("code", ErrorCode.FORBIDDEN_SCOPE)
        super().__init__(message, details=details, **kwargs)


class NotFoundError(RobotError):
    default_code = ErrorCode.NOT_FOUND
    default_severity = ErrorSeverity.WARN

    def __init__(
        self,
        message: Optional[str] = None,
        resource: Optional[str] = None,
        identifier: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if resource is not None:
            details.setdefault("resource", resource)
        if identifier is not None:
            details.setdefault("id", str(identifier))
        if message is None:
            if resource and identifier is not None:
                message = "%s %s does not exist" % (resource, identifier)
            elif resource:
                message = "%s does not exist" % (resource,)
            else:
                message = "Resource does not exist"
        kwargs.setdefault("code", ErrorCode.NOT_FOUND)
        super().__init__(message, details=details, **kwargs)


class ConflictError(RobotError):
    default_code = ErrorCode.ROBOT_BUSY
    default_severity = ErrorSeverity.WARN

    ALLOWED_CODES = (
        ErrorCode.ROBOT_BUSY,
        ErrorCode.SAFETY_INTERLOCK,
        ErrorCode.MAP_VERSION_STALE,
    )

    def __init__(self, message: str, code: Optional[ErrorCode] = None, **kwargs: Any) -> None:
        resolved = ErrorCode(code) if code is not None else self.default_code
        if resolved not in self.ALLOWED_CODES:
            raise ValueError(
                "ConflictError code must be one of %s, got %r"
                % (", ".join(c.value for c in self.ALLOWED_CODES), resolved)
            )
        super().__init__(message, code=resolved, **kwargs)


class RobotBusyError(ConflictError):
    default_code = ErrorCode.ROBOT_BUSY

    def __init__(
        self,
        message: str = "Another action is running",
        running_action_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if running_action_id is not None:
            details.setdefault("running_action_id", str(running_action_id))
        kwargs.setdefault("code", ErrorCode.ROBOT_BUSY)
        super().__init__(message, details=details, **kwargs)


class SafetyInterlockError(ConflictError):
    default_code = ErrorCode.SAFETY_INTERLOCK
    default_severity = ErrorSeverity.WARN

    def __init__(
        self,
        message: str = "Refused by safety interlock",
        interlocks: Optional[Iterable[str]] = None,
        required_flag: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if interlocks is not None:
            details.setdefault("active_interlocks", sorted(str(i) for i in interlocks))
        if required_flag is not None:
            details.setdefault("required_config_flag", required_flag)
        kwargs.setdefault("code", ErrorCode.SAFETY_INTERLOCK)
        super().__init__(message, details=details, **kwargs)


class MapVersionStaleError(ConflictError):
    default_code = ErrorCode.MAP_VERSION_STALE

    def __init__(
        self,
        message: Optional[str] = None,
        requested: Optional[int] = None,
        current: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if requested is not None:
            details.setdefault("requested_map_version", int(requested))
        if current is not None:
            details.setdefault("current_map_version", int(current))
        if message is None:
            message = "Map version %s is stale; the current map version is %s." % (
                requested,
                current,
            )
        kwargs.setdefault("code", ErrorCode.MAP_VERSION_STALE)
        super().__init__(message, details=details, **kwargs)


class UnsafeRequestError(RobotError):
    default_code = ErrorCode.UNSAFE_REQUEST
    default_severity = ErrorSeverity.WARN

    def __init__(self, message: str, reason: Optional[str] = None, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if reason is not None:
            details.setdefault("reason", reason)
        kwargs.setdefault("code", ErrorCode.UNSAFE_REQUEST)
        super().__init__(message, details=details, **kwargs)


class NotReadyError(RobotError):
    default_code = ErrorCode.NOT_READY
    default_severity = ErrorSeverity.WARN

    def __init__(
        self,
        message: str = "Not ready",
        capability: Optional[str] = None,
        retry_after_s: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if capability is not None:
            details.setdefault("capability", capability)
        if retry_after_s is not None:
            details.setdefault("retry_after_s", float(retry_after_s))
        kwargs.setdefault("code", ErrorCode.NOT_READY)
        super().__init__(message, details=details, **kwargs)


class CapabilityAbsentError(RobotError):
    default_code = ErrorCode.CAPABILITY_ABSENT
    default_severity = ErrorSeverity.WARN

    def __init__(
        self, message: Optional[str] = None, capability: Optional[str] = None, **kwargs: Any
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if capability is not None:
            details.setdefault("capability", capability)
        if message is None:
            message = (
                "%s is not available on this robot" % (capability,)
                if capability
                else "Capability is not available on this robot"
            )
        kwargs.setdefault("code", ErrorCode.CAPABILITY_ABSENT)
        kwargs.setdefault("retryable", False)
        super().__init__(message, details=details, **kwargs)


class HardwareFaultError(RobotError):
    default_code = ErrorCode.HARDWARE_FAULT
    default_severity = ErrorSeverity.ERROR

    def __init__(self, message: str, device_id: Optional[str] = None, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if device_id is not None:
            details.setdefault("device_id", device_id)
        kwargs.setdefault("code", ErrorCode.HARDWARE_FAULT)
        super().__init__(message, details=details, **kwargs)


class AdapterError(RobotError):
    default_code = ErrorCode.ADAPTER_ERROR
    default_severity = ErrorSeverity.ERROR

    def __init__(
        self,
        message: str,
        adapter: Optional[str] = None,
        operation: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if adapter is not None:
            details.setdefault("adapter", adapter)
        if operation is not None:
            details.setdefault("operation", operation)
        kwargs.setdefault("code", ErrorCode.ADAPTER_ERROR)
        super().__init__(message, details=details, **kwargs)


class InternalError(RobotError):
    default_code = ErrorCode.INTERNAL_ERROR
    default_severity = ErrorSeverity.ERROR

    def __init__(self, message: str = "Internal error", **kwargs: Any) -> None:
        kwargs.setdefault("code", ErrorCode.INTERNAL_ERROR)
        super().__init__(message, **kwargs)


def to_robot_error(exc: BaseException, source: Optional[str] = None) -> RobotError:
    if isinstance(exc, RobotError):
        if source and not exc.source:
            exc.source = source
        return exc
    return InternalError(
        "%s: %s" % (type(exc).__name__, exc),
        source=source,
        details={"exception_type": type(exc).__name__},
        cause=exc,
    )
