"""The application factory: one ASGI app, one robot core.

Construction performs no I/O; the adapter is built and the core started inside
the lifespan.
"""

from __future__ import annotations

import fnmatch
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from g1_api import __version__
from g1_api.adapters import build_adapter
from g1_api.config import AppConfig, get_config
from g1_api.core.core import RobotCore
from g1_api.errors import RobotError, to_robot_error
from g1_api.gateway.problems import (
    error_response,
    validation_error_from_request_validation,
)
from g1_api.services import ServiceRegistry
from g1_api.utils.logging import setup_logging

__all__ = ["create_app", "ROUTER_MODULES"]

_log = logging.getLogger("g1_api.gateway.app")

ROUTER_MODULES: Tuple[str, ...] = (
    "motion",
    "slam",
    "system",
    "voice",
    "arm",
    "external_actions",
    "hand",
    "tour",
)


def _cors_kwargs(config: AppConfig) -> Dict[str, Any]:
    origins = list(config.server.cors_origins)
    if "*" in origins:
        return {
            "allow_origins": ["*"],
            "allow_credentials": False,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }
    exact = [o for o in origins if "*" not in o]
    patterns = [o for o in origins if "*" in o]
    kwargs: Dict[str, Any] = {
        "allow_origins": exact,
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Request-Id"],
    }
    if patterns:
        kwargs["allow_origin_regex"] = "|".join(
            "(?:%s)" % fnmatch.translate(pattern) for pattern in patterns
        )
    return kwargs


def create_app(config: Optional[AppConfig] = None, configure_logging: bool = True) -> FastAPI:
    resolved = config if config is not None else get_config()
    if configure_logging:
        setup_logging(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        adapter = build_adapter(resolved)
        core = RobotCore(config=resolved, adapter=adapter)
        registry = ServiceRegistry(core)
        application.state.config = resolved
        application.state.core = core
        application.state.services = registry
        _log.info("starting robot core (adapter=%s)", resolved.mode.mode)
        await registry.start()
        _log.info("robot core ready")
        try:
            yield
        finally:
            _log.info("stopping robot core")
            await registry.stop()
            application.state.services = None

    app = FastAPI(
        title="g1_api",
        version=__version__,
        description=(
            "Slamtec-style core API for the Unitree G1 humanoid robot: "
            "asynchronous actions, `.g1map` map packages, localization, TTS, "
            "arm and hand commands, and a guided tour application."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.config = resolved

    install_exception_handlers(app)

    from importlib import import_module

    for name in ROUTER_MODULES:
        module = import_module("g1_api.gateway.routers.%s" % name)
        app.include_router(module.router)

    install_console(app, resolved)

    @app.get("/", include_in_schema=False)
    async def root() -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "name": "g1_api",
            "version": __version__,
            "mode": resolved.mode.mode,
            "sdk_console": "/sdk" if resolved.server.enable_sdk_console else None,
            "docs": "/docs",
        }
        return body

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> Dict[str, Any]:
        core = getattr(app.state, "core", None)
        ready = bool(core is not None and core.ready())
        return {"status": "ok", "ready": ready, "version": __version__, "mode": resolved.mode.mode}

    app.add_middleware(CORSMiddleware, **_cors_kwargs(resolved))
    return app


def install_exception_handlers(app: FastAPI) -> None:
    async def _robot_error(request: Request, exc: RobotError) -> Response:
        request_id = getattr(request.state, "request_id", None)
        return error_response(exc, request, request_id)

    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        error = validation_error_from_request_validation(exc)
        request_id = getattr(request.state, "request_id", None)
        return error_response(error, request, request_id)

    async def _unhandled(request: Request, exc: Exception) -> Response:
        error = to_robot_error(exc, source="gateway")
        request_id = getattr(request.state, "request_id", None)
        _log.exception("unhandled exception serving %s %s", request.method, request.url.path)
        return error_response(error, request, request_id)

    app.add_exception_handler(RobotError, _robot_error)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(Exception, _unhandled)


def install_console(app: FastAPI, config: AppConfig) -> None:
    if not bool(getattr(config.server, "enable_sdk_console", False)):
        return
    try:
        from g1_api.console.router import install_console as _install

        _install(app, config)
    except Exception:  # noqa: BLE001
        _log.warning("the SDK console failed to mount", exc_info=True)
