"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Request

from g1_api.errors import NotReadyError


def get_services(request: Request) -> Any:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise NotReadyError(
            "service registry is not ready", capability="services", source="gateway.deps"
        )
    return services


def get_request_id(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)
