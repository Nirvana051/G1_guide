"""Adapter contract and the two backends (mock default, g1 real)."""

from __future__ import annotations

from g1_api.config import AppConfig


def build_adapter(config: AppConfig):
    """Build the adapter for the configured mode, importing the real backend
    lazily so ``import g1_api`` never requires ROS/DDS on this machine."""
    if config.mode.is_real:
        from g1_api.adapters.g1.adapter import G1Adapter

        return G1Adapter(config)
    from g1_api.adapters.mock.adapter import MockAdapter

    return MockAdapter(config)


__all__ = ["build_adapter"]
