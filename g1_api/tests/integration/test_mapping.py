"""Mapping (map recording) session over HTTP: the SDK-driven建图 flow."""

from __future__ import annotations

from typing import Any


def _start(client: Any) -> Any:
    return client.post("/api/core/slam/v1/mapping/:start")


def test_mapping_full_flow_registers_map(client: Any) -> None:
    # idle before
    status = client.get("/api/core/slam/v1/mapping").json()
    assert status["active"] is False

    resp = _start(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is True

    # double start conflicts
    assert _start(client).status_code == 409

    # status reflects the running session
    status = client.get("/api/core/slam/v1/mapping").json()
    assert status["active"] is True

    # navigation is refused while mapping (mapping_running interlock)...
    nav = client.post(
        "/api/core/motion/v1/actions",
        json={"action_name": "g1.actions.MoveToAction",
              "options": {"target": {"x": 1.0, "y": 0.0}, "yaw": 0.0, "with_yaw": False}},
    )
    assert nav.status_code == 400
    assert "mapping_running" in nav.text

    # ...but teleop (MoveAction) stays available to drive the run.
    tele = client.post(
        "/api/core/motion/v1/actions",
        json={"action_name": "g1.actions.MoveAction",
              "options": {"vx": 0.3, "vy": 0.0, "omega": 0.0, "duration_ms": 60}},
    )
    assert tele.status_code == 200, tele.text

    # map switching is refused mid-session
    sel = client.post("/api/core/slam/v1/maps/whatever/:select")
    assert sel.status_code == 409

    # finish -> map registered in the library
    resp = client.post(
        "/api/core/slam/v1/mapping/:finish",
        json={"map_id": "mapped-room", "name": "录制的房间"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registered"] is True
    assert body["map"]["map_id"] == "mapped-room"

    maps = client.get("/api/core/slam/v1/maps").json()["maps"]
    assert "mapped-room" in [m["map_id"] for m in maps]

    # the recorded map is immediately usable
    assert client.post("/api/core/slam/v1/maps/mapped-room/:select").status_code == 200
    assert client.post(
        "/api/core/slam/v1/localization/:relocalize", json={"x": 0, "y": 0, "yaw": 0}
    ).status_code == 200

    # and the package round-trips
    pkg = client.get("/api/core/slam/v1/maps/mapped-room/package")
    assert pkg.status_code == 200
    assert len(pkg.content) > 0


def test_mapping_cancel_discards(client: Any) -> None:
    assert _start(client).status_code == 200
    resp = client.post("/api/core/slam/v1/mapping/:cancel")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
    assert client.get("/api/core/slam/v1/mapping").json()["active"] is False
    # cancel with no session running conflicts
    assert client.post("/api/core/slam/v1/mapping/:cancel").status_code == 409


def test_mapping_requires_map_write_flag(locked_config: Any) -> None:
    from tests.support.harness import make_client

    with make_client(locked_config) as locked:
        resp = locked.post("/api/core/slam/v1/mapping/:start")
        assert resp.status_code == 409
        assert "map_write_not_allowed" in resp.text
