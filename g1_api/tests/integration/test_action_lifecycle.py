"""The Slamtec asynchronous action pattern, exercised over HTTP."""

from __future__ import annotations

import time
from typing import Any

from tests.support.harness import upload_select_relocalize, wait_until


def _submit_move_to(client: Any, x: float = 0.5) -> Any:
    return client.post(
        "/api/core/motion/v1/actions",
        json={
            "action_name": "g1.actions.MoveToAction",
            "options": {"target": {"x": x, "y": 0.0}, "yaw": 0.0, "with_yaw": True,
                        "reach_threshold": 0.2, "timeout_s": 30},
        },
    )


def test_idle_current_is_404_json_string(client: Any) -> None:
    resp = client.get("/api/core/motion/v1/actions/:current")
    assert resp.status_code == 404
    assert resp.text == '"Action Not Found"'


def test_action_factories_list(client: Any) -> None:
    resp = client.get("/api/core/motion/v1/action-factories")
    assert resp.status_code == 200
    names = [f["action_name"] for f in resp.json()["action_factories"]]
    assert "g1.actions.MoveAction" in names
    assert "g1.actions.MoveToAction" in names
    assert "g1.actions.RotateAction" in names
    assert "g1.actions.RotateToAction" in names


def test_move_to_succeeds_and_reaches_terminal(client: Any, core: Any) -> None:
    upload_select_relocalize(client, "map-life")
    resp = _submit_move_to(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["action_id"], int)
    assert body["state"]["status"] == 0

    aid = body["action_id"]
    record = wait_until(
        lambda: client.get("/api/core/motion/v1/actions/%d" % aid).json()["state"]["status"] == 4
        and client.get("/api/core/motion/v1/actions/%d" % aid).json(),
        description="action to reach terminal",
    )
    assert record["state"]["status"] == 4
    assert record["state"]["result"] == 0


def test_slamtec_alias_action_name_is_accepted(client: Any) -> None:
    upload_select_relocalize(client, "map-alias")
    resp = client.post(
        "/api/core/motion/v1/actions",
        json={
            "action_name": "slamtec.agent.actions.MoveToAction",
            "options": {"target": {"x": 0.3, "y": 0.0}, "yaw": 0.0, "with_yaw": False, "timeout_s": 30},
        },
    )
    assert resp.status_code == 200, resp.text
    aid = resp.json()["action_id"]
    # Wait for it to finish so the shared client is idle for the next test.
    wait_until(lambda: client.get("/api/core/motion/v1/actions/%d" % aid).json()["state"]["status"] == 4)


def test_busy_submission_is_400_with_robot_busy_code(client: Any) -> None:
    upload_select_relocalize(client, "map-busy")
    # A long move holds the single slot.
    resp = client.post(
        "/api/core/motion/v1/actions",
        json={
            "action_name": "g1.actions.MoveToAction",
            "options": {"target": {"x": 5.0, "y": 0.0}, "yaw": 0.0, "with_yaw": False, "timeout_s": 60},
        },
    )
    assert resp.status_code == 200
    resp2 = _submit_move_to(client)
    assert resp2.status_code == 400
    assert resp2.json()["error"] == "Can not create action"
    assert resp2.json()["code"] == "ROBOT_BUSY"
    # Clean up: cancel the still-running action so the shared client is idle again.
    client.delete("/api/core/motion/v1/actions/:current")


def test_cancel_current_sets_result_minus_2(client: Any) -> None:
    upload_select_relocalize(client, "map-cancel")
    resp = client.post(
        "/api/core/motion/v1/actions",
        json={
            "action_name": "g1.actions.MoveToAction",
            "options": {"target": {"x": 5.0, "y": 0.0}, "yaw": 0.0, "with_yaw": False, "timeout_s": 60},
        },
    )
    assert resp.status_code == 200
    resp = client.delete("/api/core/motion/v1/actions/:current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["status"] == 4
    assert body["state"]["result"] == -2


def test_move_below_deadzone_is_400(client: Any) -> None:
    upload_select_relocalize(client, "map-deadzone")
    resp = client.post(
        "/api/core/motion/v1/actions",
        json={
            "action_name": "g1.actions.MoveAction",
            "options": {"vx": 0.05, "vy": 0.0, "omega": 0.0, "duration_ms": 500},
        },
    )
    # Below deadzone: the request is rejected at validation/gate time. In mock
    # the deadzone is enforced by the safety config; here we assert it is refused.
    assert resp.status_code in (400, 409, 422), resp.text


def test_rotate_action_is_relative(client: Any, core: Any) -> None:
    upload_select_relocalize(client, "map-rot")
    client.post("/api/core/slam/v1/localization/:relocalize", json={"x": 0, "y": 0, "yaw": 0.0})
    resp = client.post(
        "/api/core/motion/v1/actions",
        json={"action_name": "g1.actions.RotateAction", "options": {"angle": 0.5}},
    )
    assert resp.status_code == 200, resp.text
    aid = resp.json()["action_id"]
    wait_until(lambda: client.get("/api/core/motion/v1/actions/%d" % aid).json()["state"]["status"] == 4)
    pose = client.get("/api/core/slam/v1/localization/pose").json()
    assert abs(pose["yaw"] - 0.5) < 0.2
