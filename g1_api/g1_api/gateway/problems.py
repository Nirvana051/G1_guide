"""RFC 9457 ``application/problem+json`` error rendering.

A malformed body is answered ``400``, not FastAPI's default ``422``. Every
failure is a :class:`~g1_api.errors.RobotError`; the two Slamtec quirks that are
deliberately preserved (404-即-空闲 and the action-submission 400 wording) are
handled by the motion router itself, not here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from g1_api.errors import (
    AuthError,
    ErrorCode,
    NotFoundError,
    RobotError,
    ScopeError,
    ValidationError,
    to_robot_error,
)

__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "problem_response",
    "error_response",
    "validation_error_from_request_validation",
    "PROBLEM_RESPONSES",
]

_log = logging.getLogger("g1_api.gateway.problems")

PROBLEM_MEDIA_TYPE = "application/problem+json"


def problem_response(
    error: RobotError,
    request: Optional[Request] = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    instance = str(request.url.path) if request is not None else None
    document = error.to_problem_detail(instance=instance, trace_id=request_id)
    headers: Dict[str, str] = {}
    if request_id:
        headers["X-Request-Id"] = request_id
    if error.http_status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=error.http_status,
        content=document,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def error_response(
    error: RobotError,
    request: Optional[Request] = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    return problem_response(error, request, request_id)


def _field_path(location: Any) -> str:
    parts = []
    for item in location or ():
        if isinstance(item, int):
            if parts:
                parts[-1] = "%s[%d]" % (parts[-1], item)
            else:
                parts.append("[%d]" % item)
        else:
            parts.append(str(item))
    return ".".join(parts)


def validation_error_from_request_validation(exc: RequestValidationError) -> ValidationError:
    raw = []
    try:
        raw = list(exc.errors())
    except Exception:  # noqa: BLE001
        raw = []
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "field": _field_path(item.get("loc")),
                "message": str(item.get("msg", "invalid value")),
                "type": str(item.get("type", "value_error")),
            }
        )
    first = entries[0] if entries else None
    message = (
        "Request validation failed: %s (%s)" % (first["message"], first["field"])
        if first
        else "Request validation failed"
    )
    error = ValidationError(message, field=first["field"] if first else None, source="gateway.validation")
    if entries:
        error.details["errors"] = entries
    return error


#: OpenAPI responses fragment for every route.
PROBLEM_RESPONSES: Dict[int, Dict[str, Any]] = {
    400: {"description": "Malformed request (syntax, types, ranges)."},
    404: {"description": "The addressed resource does not exist."},
    409: {"description": "Valid request, wrong state: ROBOT_BUSY or SAFETY_INTERLOCK."},
    422: {"description": "Semantically refused: UNSAFE_REQUEST."},
    500: {"description": "Adapter, hardware or internal failure."},
    501: {"description": "Capability absent on this robot."},
    503: {"description": "Capability still initialising."},
}
