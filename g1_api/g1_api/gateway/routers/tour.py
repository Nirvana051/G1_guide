"""Tour router: the guided-tour application layer."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from g1_api.errors import ErrorCode, RobotError
from g1_api.gateway.deps import get_services
from g1_api.models.tour import Tour, TourPoint

router = APIRouter()


def _problem(exc: RobotError, instance: Optional[str] = None) -> JSONResponse:
    return JSONResponse(
        exc.to_problem_detail(instance=instance),
        status_code=exc.http_status,
        media_type="application/problem+json",
    )


def _tour_from_body(body: dict, map_id: str) -> Tour:
    points = tuple(TourPoint.from_dict(p) for p in body.get("points", ()) or ())
    return Tour(
        tour_id=map_id,
        map_id=map_id,
        points=points,
        loop=bool(body.get("loop", False)),
        on_failure=str(body.get("on_failure", "retry_once")),
    )


@router.get("/api/tour/v1/tours", include_in_schema=True)
async def get_tour(request: Request) -> Response:
    services = get_services(request)
    tour = await services.tour.get_tour()
    if tour is None:
        return JSONResponse({"tour": None})
    return JSONResponse({"tour": tour.to_dict()})


@router.put("/api/tour/v1/tours", include_in_schema=True)
async def put_tour(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    current = await services.map.current_map()
    if current is None:
        return _problem(
            RobotError("no map selected", code=ErrorCode.SAFETY_INTERLOCK, http_status=409),
            "/api/tour/v1/tours",
        )
    tour = _tour_from_body(body, current.map_id)
    try:
        saved = await services.tour.put_tour(tour, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/tour/v1/tours")
    return JSONResponse({"tour": saved.to_dict()})


@router.post("/api/tour/v1/tours/:start", include_in_schema=True)
async def start_tour(request: Request) -> Response:
    services = get_services(request)
    try:
        state = await services.tour.start(caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/tour/v1/tours/:start")
    return JSONResponse(state.to_dict())


@router.post("/api/tour/v1/points/{index}/:test", include_in_schema=True)
async def test_tour_point(request: Request, index: int) -> Response:
    """Rehearse one tour point (single-point tour: poll /tours/:current, stop
    with /tours/:stop). DEFAULT is in place -- actions + TTS + dwell only, no
    walking; pass ``nav=1`` to also navigate to the point first."""
    services = get_services(request)
    with_nav = request.query_params.get("nav", "0") in ("1", "true", "yes")
    try:
        state = await services.tour.test_point(index, with_nav=with_nav, caller="anonymous")
    except RobotError as exc:
        return _problem(exc, "/api/tour/v1/points/%d/:test" % index)
    return JSONResponse(state.to_dict())


@router.post("/api/tour/v1/tours/:pause", include_in_schema=True)
async def pause_tour(request: Request) -> Response:
    services = get_services(request)
    try:
        state = await services.tour.pause()
    except RobotError as exc:
        return _problem(exc, "/api/tour/v1/tours/:pause")
    return JSONResponse(state.to_dict())


@router.post("/api/tour/v1/tours/:resume", include_in_schema=True)
async def resume_tour(request: Request) -> Response:
    services = get_services(request)
    try:
        state = await services.tour.resume()
    except RobotError as exc:
        return _problem(exc, "/api/tour/v1/tours/:resume")
    return JSONResponse(state.to_dict())


@router.post("/api/tour/v1/tours/:stop", include_in_schema=True)
async def stop_tour(request: Request) -> Response:
    services = get_services(request)
    try:
        state = await services.tour.stop()
    except RobotError as exc:
        return _problem(exc, "/api/tour/v1/tours/:stop")
    return JSONResponse(state.to_dict())


@router.get("/api/tour/v1/tours/:current", include_in_schema=True)
async def current_tour(request: Request) -> JSONResponse:
    services = get_services(request)
    return JSONResponse(services.tour.current().to_dict())


@router.get("/api/tour/v1/events", include_in_schema=True)
async def tour_events(request: Request) -> JSONResponse:
    services = get_services(request)
    from_cursor = request.query_params.get("from_cursor")
    events = services.core.events.history(from_cursor=from_cursor)
    return JSONResponse({"events": [e.to_dict() for e in events]})
