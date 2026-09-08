"""Shared pytest fixtures. Everything non-fixture lives in tests.support.harness."""

from __future__ import annotations

from typing import Any, Callable, Iterator

import pytest

from tests.support.harness import (  # noqa: F401
    DEFAULT_WAIT_S,
    PACKAGE_ROOT,
    PROJECT_ROOT,
    AppConfig,
    ManualClock,
    NetworkAccessAttempted,
    build_config,
    install_network_guard,
    make_client,
    wait_until,
)


@pytest.fixture(scope="session", autouse=True)
def _no_network() -> Iterator[None]:
    undo = install_network_guard()
    try:
        yield
    finally:
        undo()


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    return build_config(allow_all=True)


@pytest.fixture(scope="session")
def locked_config() -> AppConfig:
    return build_config(allow_all=False)


@pytest.fixture
def frozen_clock() -> ManualClock:
    return ManualClock(mono_ns=1_000_000_000, boot_id="test-boot", name="test")


@pytest.fixture(scope="module")
def client(app_config: AppConfig) -> Iterator[Any]:
    with make_client(app_config) as started:
        yield started


@pytest.fixture(scope="module")
def app(client: Any) -> Any:
    return client.app


@pytest.fixture(scope="module")
def services(client: Any) -> Any:
    return client.app.state.services


@pytest.fixture(scope="module")
def core(client: Any) -> Any:
    return client.app.state.core


@pytest.fixture(scope="module")
def mock_world(core: Any) -> Any:
    world = getattr(core.adapter, "world", None)
    if world is None:
        pytest.skip("the active adapter is not the mock simulator")
    return world


@pytest.fixture
def wait_for() -> Callable[..., Any]:
    return wait_until
