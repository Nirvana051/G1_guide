"""Everything the suite shares that is not a pytest fixture.

* No network ever: ``install_network_guard`` makes an INET socket impossible.
* The application gets a deterministic clock (real monotonic, pinned wall clock
  and boot id) so the simulated chassis can integrate its own motion.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from typing import Any, Callable, Dict, Iterator, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

PACKAGE_ROOT = os.path.join(PROJECT_ROOT, "g1_api")

from g1_api.config import (  # noqa: E402
    AppConfig,
    HandConfig,
    IdentityConfig,
    LoggingConfig,
    MapConfig,
    ModeConfig,
    SafetyConfig,
    ServerConfig,
    TourConfig,
    VoiceConfig,
)
from g1_api.core.clock import Clock, ManualClock  # noqa: E402

__all__ = [
    "PROJECT_ROOT",
    "PACKAGE_ROOT",
    "TEST_EPOCH_UTC_NS",
    "TEST_BOOT_ID",
    "DEFAULT_WAIT_S",
    "NetworkAccessAttempted",
    "install_network_guard",
    "make_deterministic_clock",
    "build_config",
    "make_client",
    "wait_until",
]

TEST_EPOCH_UTC_NS = ManualClock.DEFAULT_UTC_NS
TEST_BOOT_ID = "test-boot-0000"
DEFAULT_WAIT_S = 30.0


class NetworkAccessAttempted(RuntimeError):
    """Raised when anything in the suite tries to open an INET socket."""


def install_network_guard() -> Callable[[], None]:
    real_socket = socket.socket

    class _BlockedSocket(real_socket):  # type: ignore[misc]
        def __init__(self, family: Any = socket.AF_INET, *args: Any, **kwargs: Any) -> None:
            if family in (socket.AF_INET, socket.AF_INET6):
                raise NetworkAccessAttempted("The test suite attempted to open an INET socket.")
            super(_BlockedSocket, self).__init__(family, *args, **kwargs)

    socket.socket = _BlockedSocket  # type: ignore[misc,assignment]

    def _undo() -> None:
        socket.socket = real_socket  # type: ignore[misc,assignment]

    return _undo


def make_deterministic_clock(name: str = "test-app") -> Clock:
    origin = time.monotonic_ns()

    def _utc() -> int:
        return TEST_EPOCH_UTC_NS + (time.monotonic_ns() - origin)

    return Clock(utc_source=_utc, boot_id=TEST_BOOT_ID, name=name)


def build_config(allow_all: bool = False, **safety_overrides: Any) -> AppConfig:
    safety_kwargs: Dict[str, Any] = {}
    if allow_all:
        safety_kwargs.update(
            allow_motion=True,
            allow_navigation=True,
            allow_map_write=True,
            allow_arm=True,
            allow_hand=True,
            allow_voice=True,
            allow_tour=True,
        )
    safety_kwargs.update(safety_overrides)
    return AppConfig(
        server=ServerConfig(host="127.0.0.1", port=1448),
        mode=ModeConfig(mode="mock"),
        safety=SafetyConfig(**safety_kwargs),
        identity=IdentityConfig(),
        map=MapConfig(map_storage_dir=os.path.join(PROJECT_ROOT, ".test_maps")),
        voice=VoiceConfig(),
        hand=HandConfig(),
        tour=TourConfig(
            external_actions_dir=os.path.join(PROJECT_ROOT, ".test_external_actions"),
        ),
        logging=LoggingConfig(level="CRITICAL"),
    )


class _CoreClockPatch(object):
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._real: Any = None
        self._module: Any = None

    def __enter__(self) -> "_CoreClockPatch":
        from g1_api.gateway import app as app_module

        self._module = app_module
        self._real = app_module.RobotCore

        real = self._real
        clock = self._clock

        def _factory(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("clock", clock)
            return real(*args, **kwargs)

        app_module.RobotCore = _factory  # type: ignore[misc,assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._module is not None:
            self._module.RobotCore = self._real  # type: ignore[misc,assignment]


def make_client(config: Optional[AppConfig] = None) -> Any:
    from fastapi.testclient import TestClient

    from g1_api.gateway.app import create_app

    resolved = config if config is not None else build_config(allow_all=True)

    class _Managed(object):
        def __init__(self) -> None:
            self._patch = _CoreClockPatch(make_deterministic_clock())
            self._client: Any = None

        def __enter__(self) -> Any:
            self._patch.__enter__()
            try:
                app = create_app(resolved, configure_logging=False)
                self._client = TestClient(app)
                self._client.__enter__()
            except Exception:
                self._patch.__exit__()
                raise
            return self._client

        def __exit__(self, *exc: Any) -> None:
            try:
                if self._client is not None:
                    self._client.__exit__(*exc)
            finally:
                self._patch.__exit__()

    return _Managed()


def build_map_package(map_id: str, tour_points: Any = None) -> bytes:
    """Build a minimal, valid ``.g1map`` for tests."""
    from g1_api.models.map import MapManifest, TourPointModel, pack_map_package

    points = tuple(tour_points or ())
    manifest = MapManifest(
        map_id=map_id,
        name="test map %s" % map_id,
        created_utc=time.time(),
        resolution=0.05,
        origin=(0.0, 0.0, 0.0),
        tour_points=points,
    )
    files = {
        "map.pcd": b"pcd",
        "ground_map.pcd": b"ground",
        "grid.pgm": b"P5\n1 1\n1\n\x00",
        "grid.yaml": b"image: grid.pgm\nresolution: 0.05\norigin: [0,0,0]\n",
    }
    return pack_map_package(manifest, files)


def upload_select_relocalize(client: Any, map_id: str, tour_points: Any = None) -> None:
    """Drive the full map + relocalize HTTP flow on a test client."""
    data = build_map_package(map_id, tour_points)
    resp = client.post("/api/core/slam/v1/maps", content=data, headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/core/slam/v1/maps/%s/:select" % map_id)
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/core/slam/v1/localization/:relocalize", json={"x": 0, "y": 0, "yaw": 0})
    assert resp.status_code == 200, resp.text


def wait_until(
    predicate: Callable[[], Any],
    timeout_s: float = DEFAULT_WAIT_S,
    interval_s: float = 0.02,
    description: str = "condition",
) -> Any:
    deadline = time.monotonic() + float(timeout_s)
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval_s)
    raise AssertionError(
        "timed out after %.1fs waiting for %s (last value: %r)"
        % (timeout_s, description, last)
    )
