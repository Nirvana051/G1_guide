"""External (replay) action library: upload / list / download / delete /
execute-by-id -- the official-arm-action usage pattern for user motions."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from g1_api.errors import RobotError
from g1_api.gateway.deps import get_services

router = APIRouter()

_BASE = "/api/core/motion/v1/external-actions"


def _problem(exc: RobotError, instance: str) -> JSONResponse:
    return JSONResponse(
        exc.to_problem_detail(instance=instance),
        status_code=exc.http_status,
        media_type="application/problem+json",
    )


@router.get(_BASE, include_in_schema=True)
async def list_external_actions(request: Request) -> JSONResponse:
    services = get_services(request)
    return JSONResponse({"actions": await services.external_actions.list_actions()})


@router.post(_BASE, include_in_schema=True)
async def upload_external_action(request: Request) -> Response:
    """Body: raw .npy bytes. Query: name (required), frequency, velocity_limit,
    description."""
    services = get_services(request)
    params = request.query_params
    data = await request.body()
    try:
        meta = await services.external_actions.upload(
            name=params.get("name", ""),
            data=data,
            frequency_hz=float(params.get("frequency", 30.0)),
            velocity_limit=float(params.get("velocity_limit", 20.0)),
            description=params.get("description", ""),
        )
    except RobotError as exc:
        return _problem(exc, _BASE)
    return JSONResponse(meta)


@router.get(_BASE + "/{action_id}", include_in_schema=True)
async def get_external_action(request: Request, action_id: str) -> Response:
    services = get_services(request)
    try:
        meta = await services.external_actions.get_meta(action_id)
    except RobotError as exc:
        return _problem(exc, _BASE + "/" + action_id)
    return JSONResponse(meta)


@router.get(_BASE + "/{action_id}/file", include_in_schema=True)
async def download_external_action(request: Request, action_id: str) -> Response:
    services = get_services(request)
    try:
        blob = await services.external_actions.get_file(action_id)
    except RobotError as exc:
        return _problem(exc, _BASE + "/" + action_id + "/file")
    return Response(content=blob, media_type="application/octet-stream")


@router.delete(_BASE + "/{action_id}", include_in_schema=True)
async def delete_external_action(request: Request, action_id: str) -> Response:
    services = get_services(request)
    try:
        await services.external_actions.delete(action_id)
    except RobotError as exc:
        return _problem(exc, _BASE + "/" + action_id)
    return JSONResponse({"deleted": action_id})


@router.post(_BASE + "/{action_id}/:execute", include_in_schema=True)
async def execute_external_action(request: Request, action_id: str) -> Response:
    """Synchronous: returns when the replay finishes (like official arm
    actions). While it runs, the external_motion_running interlock refuses
    official arm actions and navigation."""
    services = get_services(request)
    try:
        result = await services.external_actions.execute(action_id, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, _BASE + "/" + action_id + "/:execute")
    return JSONResponse(result)
