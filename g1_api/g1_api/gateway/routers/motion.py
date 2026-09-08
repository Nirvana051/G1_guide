"""Motion router: the Slamtec asynchronous action pattern, wire-faithful.

The two Slamtec quirks preserved on purpose:

* ``GET /actions/:current`` answers ``404`` with the JSON string
  ``"Action Not Found"`` when idle -- normal control flow, not an error.
* a refused submission answers ``400 "Can not create action"`` with a
  machine-readable ``code`` (``ROBOT_BUSY`` / ``SAFETY_INTERLOCK`` /
  ``VALIDATION_ERROR``), which is the mild correction over the reference's
  overloaded 400.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from g1_api.errors import RobotError, RobotBusyError, SafetyInterlockError, ValidationError
from g1_api.gateway.deps import get_services
from g1_api.models.action import ActionRecord

router = APIRouter()


def _compat_status_result(record: ActionRecord) -> Dict[str, Any]:
    from g1_api.models.enums import COMPAT_STATUS_RESULT

    status, result = COMPAT_STATUS_RESULT.get(record.lifecycle, (4, -1))
    return {"status": status, "result": result, "reason": record.reason}


def record_to_action_info(record: ActionRecord) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "action_id": record.compat_id,
        "action_name": record.action_name,
        "stage": record.progress.stage_text or str(record.progress.stage),
        "state": _compat_status_result(record),
    }
    if record.failure_code is not None:
        info["failure_code"] = str(record.failure_code)
    return info


def _slamtec_400(error: RobotError) -> Response:
    return JSONResponse(
        status_code=400,
        content={
            "error": "Can not create action",
            "code": str(error.code.value),
            "message": error.message,
            "details": error.details,
        },
    )


@router.post("/api/core/motion/v1/actions", include_in_schema=True)
async def submit_action(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    action_name = str(body.get("action_name", ""))
    options = body.get("options")
    try:
        record = await services.motion.submit(action_name, options, requester="anonymous")
    except (RobotBusyError, SafetyInterlockError, ValidationError) as exc:
        return _slamtec_400(exc)
    return JSONResponse(record_to_action_info(record))


@router.get("/api/core/motion/v1/actions/:current", include_in_schema=True)
async def get_current_action(request: Request) -> Response:
    services = get_services(request)
    record = services.motion.current()
    if record is None:
        return Response(
            content=b'"Action Not Found"',
            status_code=404,
            media_type="application/json",
        )
    return JSONResponse(record_to_action_info(record))


@router.delete("/api/core/motion/v1/actions/:current", include_in_schema=True)
async def cancel_current_action(request: Request) -> Response:
    services = get_services(request)
    try:
        record = await services.motion.cancel_current()
    except RobotError as exc:
        return JSONResponse(
            exc.to_problem_detail(instance="/api/core/motion/v1/actions/:current"),
            status_code=exc.http_status,
            media_type="application/problem+json",
        )
    return JSONResponse(record_to_action_info(record))


@router.get("/api/core/motion/v1/actions/{action_id}", include_in_schema=True)
async def get_action(action_id: int, request: Request) -> Response:
    services = get_services(request)
    try:
        record = services.motion.get_by_compat_id(action_id)
    except RobotError as exc:
        return JSONResponse(
            exc.to_problem_detail(),
            status_code=exc.http_status,
            media_type="application/problem+json",
        )
    return JSONResponse(record_to_action_info(record))


@router.get("/api/core/motion/v1/action-factories", include_in_schema=True)
async def list_action_factories(request: Request) -> Response:
    services = get_services(request)
    factories = services.motion.list_factories(include_schema=True)
    return JSONResponse({"action_factories": factories})
