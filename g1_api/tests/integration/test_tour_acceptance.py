"""The guided-tour acceptance scenario: select -> relocalize -> start -> run."""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Iterator

import pytest

from g1_api.config import TourConfig
from g1_api.models.map import TourPointModel
from tests.support.harness import (
    build_config,
    make_client,
    upload_select_relocalize,
    wait_until,
)


def _tour_points() -> list:
    return [
        TourPointModel(name="点1", x=0.5, y=0.0, yaw=0.0, actions=[{"type": "arm", "id": 26}], tts_text="第一站"),
        TourPointModel(name="点2", x=1.0, y=0.0, yaw=0.0, actions=[{"type": "hand", "cmd": "7"}], tts_text="第二站"),
        TourPointModel(name="点3", x=1.5, y=0.0, yaw=0.0, actions=[{"type": "arm", "id": 26}], tts_text="第三站"),
    ]


@pytest.fixture(scope="module")
def tour_client() -> Iterator[Any]:
    # A fast arm-release wait keeps the acceptance run quick while still going
    # through the real release-after-every-action path.
    config = build_config(allow_all=True)
    config = dataclasses.replace(config, tour=TourConfig(arm_release_wait_s=0.05))
    with make_client(config) as client:
        yield client


@pytest.fixture(scope="module")
def tour_world(tour_client: Any) -> Any:
    return tour_client.app.state.core.adapter.world


def _start(tour_client: Any, on_failure: str = "retry_once") -> Any:
    tour_client.put(
        "/api/tour/v1/tours",
        json={"points": [p.to_dict() for p in _tour_points()], "on_failure": on_failure},
    )
    resp = tour_client.post("/api/tour/v1/tours/:start")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _wait_terminal(tour_client: Any) -> dict:
    terminal = ("finished", "failed", "stopped")
    return wait_until(
        lambda: (
            (lambda s: s if s.get("status") in terminal else None)(
                tour_client.get("/api/tour/v1/tours/:current").json()
            )
        ),
        description="tour to finish",
        timeout_s=40,
    )


def test_full_three_point_flow(tour_client: Any) -> None:
    upload_select_relocalize(tour_client, "map-tour-full", _tour_points())
    _start(tour_client)
    state = _wait_terminal(tour_client)
    assert state["status"] == "finished"
    statuses = [p["status"] for p in state["points"]]
    assert statuses == ["succeeded", "succeeded", "succeeded"]
    # Arrival orientation is correct (yaw 0.0).
    pose = tour_client.get("/api/core/slam/v1/localization/pose").json()
    assert abs(pose["yaw"]) < 0.15
    # Events were published.
    events = [e["type"] for e in tour_client.get("/api/tour/v1/events").json()["events"]]
    assert "point_reached" in events
    assert "tts_started" in events
    assert "action_done" in events


def test_pause_resume_stop(tour_client: Any) -> None:
    upload_select_relocalize(tour_client, "map-tour-pause", _tour_points())
    _start(tour_client)
    resp = tour_client.post("/api/tour/v1/tours/:pause")
    assert resp.json()["status"] == "paused"
    time.sleep(0.2)
    resp = tour_client.post("/api/tour/v1/tours/:resume")
    assert resp.json()["status"] == "running"
    resp = tour_client.post("/api/tour/v1/tours/:stop")
    assert resp.json()["status"] == "stopped"


def test_on_failure_skip(tour_client: Any, tour_world: Any) -> None:
    upload_select_relocalize(tour_client, "map-tour-skip", _tour_points())
    tour_world.set_fault_goal_unreachable(True)
    _start(tour_client, on_failure="skip")
    state = _wait_terminal(tour_client)
    tour_world.set_fault_goal_unreachable(False)
    statuses = [p["status"] for p in state["points"]]
    assert statuses[0] == "skipped"
    assert state["status"] == "finished"


def test_on_failure_abort(tour_client: Any, tour_world: Any) -> None:
    upload_select_relocalize(tour_client, "map-tour-abort", _tour_points())
    tour_world.set_fault_goal_unreachable(True)
    _start(tour_client, on_failure="abort")
    state = _wait_terminal(tour_client)
    tour_world.set_fault_goal_unreachable(False)
    assert state["status"] == "failed"
    assert state["points"][0]["status"] == "failed"


def test_on_failure_retry_once(tour_client: Any, tour_world: Any) -> None:
    upload_select_relocalize(tour_client, "map-tour-retry", _tour_points())
    # Fail exactly one goal; the retry should then succeed.
    tour_world.fail_next_goal(1)
    _start(tour_client, on_failure="retry_once")
    state = _wait_terminal(tour_client)
    assert state["points"][0]["status"] == "succeeded"
    assert state["points"][0]["attempts"] == 2


def test_preflight_requires_map_and_localization() -> None:
    # A fresh, permissive client with no map selected must refuse to start.
    config = build_config(allow_all=True)
    with make_client(config) as fresh:
        resp = fresh.post("/api/tour/v1/tours/:start")
        assert resp.status_code == 409, resp.text
        assert "map_not_selected" in resp.json().get("details", {}).get("active_interlocks", [])


def test_tour_arm_action_is_still_gated() -> None:
    # The tour must NOT bypass the safety gate: with allow_arm=False the tour's
    # own arm action is refused, the point fails and the tour aborts.
    config = build_config(
        allow_navigation=True, allow_map_write=True, allow_tour=True,
        allow_hand=True, allow_voice=True, allow_arm=False,
    )
    config = dataclasses.replace(config, tour=TourConfig(arm_release_wait_s=0.05))
    with make_client(config) as fresh:
        upload_select_relocalize(fresh, "map-tour-gated", _tour_points())
        fresh.put("/api/tour/v1/tours", json={"points": [p.to_dict() for p in _tour_points()], "on_failure": "abort"})
        resp = fresh.post("/api/tour/v1/tours/:start")
        assert resp.status_code == 200, resp.text
        state = wait_until(
            lambda: (
                (lambda s: s if s.get("status") in ("finished", "failed", "stopped") else None)(
                    fresh.get("/api/tour/v1/tours/:current").json()
                )
            ),
            description="tour to abort on gated arm action",
        )
        assert state["status"] == "failed"
        assert state["points"][0]["status"] == "failed"
        assert "arm_not_allowed" in state["points"][0]["reason"]


def test_manual_action_is_refused_during_tour(tour_client: Any) -> None:
    upload_select_relocalize(tour_client, "map-tour-manual", _tour_points())
    _start(tour_client)
    # A manual navigation command while the tour runs must be refused with
    # the tour_running interlock.
    resp = tour_client.post(
        "/api/core/motion/v1/actions",
        json={
            "action_name": "g1.actions.MoveToAction",
            "options": {"target": {"x": 9, "y": 9}, "yaw": 0.0, "with_yaw": False, "timeout_s": 30},
        },
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "SAFETY_INTERLOCK"
    tour_client.post("/api/tour/v1/tours/:stop")
    _wait_terminal(tour_client)
