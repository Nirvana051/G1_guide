"""Arm router: list actions, execute one."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from g1_api.errors import RobotError
from g1_api.gateway.deps import get_services

router = APIRouter()


@router.get("/api/core/arm/v1/action-list", include_in_schema=True)
async def action_list(request: Request) -> JSONResponse:
    services = get_services(request)
    actions = await services.arm.get_action_list()
    return JSONResponse({"actions": [{"id": k, "name": v} for k, v in actions.items()]})


@router.post("/api/core/arm/v1/actions", include_in_schema=True)
async def execute_arm_action(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    action_id = int(body.get("action_id", 0) or 0)
    try:
        result = await services.arm.execute_action(action_id, caller="anonymous")
    except RobotError as exc:
        return JSONResponse(
            exc.to_problem_detail(instance="/api/core/arm/v1/actions"),
            status_code=exc.http_status,
            media_type="application/problem+json",
        )
    return JSONResponse(result)
