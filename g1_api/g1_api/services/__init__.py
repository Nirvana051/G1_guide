"""Service layer: use-case logic over the core. Services return canonical models
and never leak wire JSON."""

from __future__ import annotations

from g1_api.services.registry import ServiceRegistry

__all__ = ["ServiceRegistry"]
