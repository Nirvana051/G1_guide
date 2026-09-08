"""System router: robot info, capabilities, health."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from g1_api.gateway.deps import get_services

router = APIRouter()


@router.get("/api/core/system/v1/robot/info", include_in_schema=True)
async def robot_info(request: Request) -> JSONResponse:
    services = get_services(request)
    info = await services.system.get_info()
    return JSONResponse(info.to_dict())


@router.get("/api/core/system/v1/capabilities", include_in_schema=True)
async def capabilities(request: Request) -> JSONResponse:
    services = get_services(request)
    caps = await services.system.get_capabilities()
    return JSONResponse({"capabilities": [c.to_dict() for c in caps]})


@router.get("/api/core/system/v1/robot/health", include_in_schema=True)
async def robot_health(request: Request) -> JSONResponse:
    services = get_services(request)
    health = await services.system.get_health()
    return JSONResponse(health.to_dict())
