"""SLAM router: maps (`.g1map` packages) and localization/relocalization."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from g1_api.errors import RobotError
from g1_api.gateway.deps import get_services
from g1_api.models.map import pack_map_package

router = APIRouter()


def _problem(exc: RobotError, instance: Optional[str] = None) -> JSONResponse:
    return JSONResponse(
        exc.to_problem_detail(instance=instance),
        status_code=exc.http_status,
        media_type="application/problem+json",
    )


@router.get("/api/core/slam/v1/maps", include_in_schema=True)
async def list_maps(request: Request) -> Response:
    services = get_services(request)
    maps = await services.map.list_maps()
    return JSONResponse({"maps": [m.to_dict() for m in maps]})


@router.post("/api/core/slam/v1/maps", include_in_schema=True)
async def upload_map(request: Request) -> Response:
    services = get_services(request)
    data = await request.body()
    try:
        manifest = await services.map.upload_map(data, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/core/slam/v1/maps")
    return JSONResponse({"map": manifest.to_dict(), "selected": False})


@router.get("/api/core/slam/v1/maps/{map_id}/package", include_in_schema=True)
async def download_map(map_id: str, request: Request) -> Response:
    services = get_services(request)
    try:
        package = await services.map.download_map(map_id)
    except RobotError as exc:
        return _problem(exc)
    data = pack_map_package(package.manifest, package.files)
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="%s.g1map"' % map_id},
    )


@router.get("/api/core/slam/v1/maps/:current", include_in_schema=True)
async def current_map(request: Request) -> Response:
    services = get_services(request)
    manifest = await services.map.current_map()
    if manifest is None:
        return JSONResponse({"map": None, "selected": False})
    return JSONResponse({"map": manifest.to_dict(), "selected": True})


@router.post("/api/core/slam/v1/maps/{map_id}/:select", include_in_schema=True)
async def select_map(map_id: str, request: Request) -> Response:
    services = get_services(request)
    try:
        manifest = await services.map.select_map(map_id, caller="anonymous")
    except RobotError as exc:
        return _problem(exc)
    return JSONResponse({"selected": True, "requires_relocalization": True, "map": manifest.to_dict()})


@router.delete("/api/core/slam/v1/maps/{map_id}", include_in_schema=True)
async def delete_map(map_id: str, request: Request) -> Response:
    services = get_services(request)
    try:
        await services.map.delete_map(map_id, caller="anonymous")
    except RobotError as exc:
        return _problem(exc)
    return JSONResponse({"deleted": True})


@router.get("/api/core/slam/v1/maps/{map_id}/grid", include_in_schema=True)
async def get_grid(map_id: str, request: Request) -> Response:
    services = get_services(request)
    try:
        pgm = await services.map.get_grid(map_id)
    except RobotError as exc:
        return _problem(exc)
    return Response(content=pgm, media_type="image/x-portable-graymap")


@router.put("/api/core/slam/v1/maps/{map_id}/grid", include_in_schema=True)
async def put_grid(map_id: str, request: Request) -> Response:
    services = get_services(request)
    data = await request.body()
    try:
        manifest = await services.map.update_grid(map_id, data, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/core/slam/v1/maps/%s/grid" % map_id)
    return JSONResponse({
        "saved": True, "map_id": manifest.map_id,
        "note": "grid saved in place; re-:select this map for the running navigation stack to load it",
    })


@router.put("/api/core/slam/v1/maps/{map_id}/tour-points", include_in_schema=True)
async def put_tour_points(map_id: str, request: Request) -> Response:
    from g1_api.models.map import TourPointModel

    services = get_services(request)
    body = await request.json()
    try:
        points = [TourPointModel.from_dict(p) for p in body.get("points", ()) or ()]
        manifest = await services.map.set_tour_points(map_id, points, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/core/slam/v1/maps/%s/tour-points" % map_id)
    return JSONResponse({"saved": True, "map": manifest.to_dict()})


@router.post("/api/core/slam/v1/mapping/:start", include_in_schema=True)
async def start_mapping(request: Request) -> Response:
    services = get_services(request)
    try:
        status = await services.map.start_mapping(caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/core/slam/v1/mapping/:start")
    return JSONResponse(status.to_dict())


@router.get("/api/core/slam/v1/mapping", include_in_schema=True)
async def mapping_status(request: Request) -> Response:
    services = get_services(request)
    status = await services.map.mapping_status()
    return JSONResponse(status.to_dict())


@router.post("/api/core/slam/v1/mapping/:finish", include_in_schema=True)
async def finish_mapping(request: Request) -> Response:
    services = get_services(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 -- empty body is fine
        body = {}
    map_id = str(body.get("map_id") or "").strip()
    if not map_id:
        import time as _time

        map_id = "map-%s" % _time.strftime("%Y%m%d-%H%M%S")
    name = str(body.get("name") or "")
    try:
        manifest = await services.map.finish_mapping(map_id, name, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/core/slam/v1/mapping/:finish")
    return JSONResponse(
        {"map": manifest.to_dict(), "registered": True,
         "note": "map saved and registered; POST /maps/%s/:select then relocalize to use it" % map_id}
    )


@router.post("/api/core/slam/v1/mapping/:cancel", include_in_schema=True)
async def cancel_mapping(request: Request) -> Response:
    services = get_services(request)
    try:
        await services.map.cancel_mapping(caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/core/slam/v1/mapping/:cancel")
    return JSONResponse({"cancelled": True})


@router.get("/api/core/slam/v1/localization/pose", include_in_schema=True)
async def localization_pose(request: Request) -> Response:
    services = get_services(request)
    pose = await services.localization.get_pose()
    return JSONResponse(
        {"x": pose.x_m, "y": pose.y_m, "z": 0.0, "yaw": pose.yaw_rad, "pitch": 0.0, "roll": 0.0}
    )


@router.post("/api/core/slam/v1/localization/:relocalize", include_in_schema=True)
async def relocalize(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    x = float(body.get("x", 0.0))
    y = float(body.get("y", 0.0))
    yaw = float(body.get("yaw", 0.0))
    try:
        status = await services.localization.relocalize(x, y, yaw, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/core/slam/v1/localization/:relocalize")
    return JSONResponse(status.to_dict())


@router.get("/api/core/slam/v1/localization/status", include_in_schema=True)
async def localization_status(request: Request) -> Response:
    services = get_services(request)
    status = await services.localization.get_status()
    return JSONResponse(status.to_dict())
