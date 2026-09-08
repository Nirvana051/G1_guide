"""Voice router: TTS, volume, LED."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from g1_api.errors import RobotError
from g1_api.gateway.deps import get_services

router = APIRouter()


@router.post("/api/core/voice/v1/tts", include_in_schema=True)
async def tts(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    text = str(body.get("text", ""))
    raw_speaker = body.get("speaker_id")
    speaker_id = int(raw_speaker) if raw_speaker is not None else None
    wait = bool(body.get("wait", False))
    try:
        result = await services.voice.tts(text, speaker_id=speaker_id, wait=wait, caller="anonymous")
    except RobotError as exc:
        return JSONResponse(
            exc.to_problem_detail(instance="/api/core/voice/v1/tts"),
            status_code=exc.http_status,
            media_type="application/problem+json",
        )
    return JSONResponse(result)


@router.get("/api/core/voice/v1/volume", include_in_schema=True)
async def get_volume(request: Request) -> JSONResponse:
    services = get_services(request)
    volume = await services.voice.get_volume()
    return JSONResponse({"volume": volume})


@router.put("/api/core/voice/v1/volume", include_in_schema=True)
async def set_volume(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    volume = int(body.get("volume", 100) or 100)
    try:
        result = await services.voice.set_volume(volume, caller="anonymous")
    except RobotError as exc:
        return JSONResponse(
            exc.to_problem_detail(instance="/api/core/voice/v1/volume"),
            status_code=exc.http_status,
            media_type="application/problem+json",
        )
    return JSONResponse({"volume": result})


@router.put("/api/core/voice/v1/led", include_in_schema=True)
async def set_led(request: Request) -> Response:
    services = get_services(request)
    body = await request.json()
    r = int(body.get("r", 0) or 0)
    g = int(body.get("g", 0) or 0)
    b = int(body.get("b", 0) or 0)
    hold_s = float(body.get("hold_s", 0) or 0)
    try:
        await services.voice.set_led(r, g, b, hold_s=hold_s, caller="anonymous")
    except RobotError as exc:
        return JSONResponse(
            exc.to_problem_detail(instance="/api/core/voice/v1/led"),
            status_code=exc.http_status,
            media_type="application/problem+json",
        )
    return JSONResponse({"led": {"r": r, "g": g, "b": b}, "hold_s": hold_s})
