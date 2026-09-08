"""Hand router: dexterous hand TCP commands."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from g1_api.errors import RobotError
from g1_api.gateway.deps import get_services

router = APIRouter()


@router.post("/api/core/hand/v1/command", include_in_schema=True)
async def hand_command(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    cmd = str(body.get("cmd", ""))
    try:
        result = await services.hand.send_command(cmd, caller="anonymous")
    except RobotError as exc:
        return JSONResponse(
            exc.to_problem_detail(instance="/api/core/hand/v1/command"),
            status_code=exc.http_status,
            media_type="application/problem+json",
        )
    return JSONResponse({"cmd": cmd, "response": result})
