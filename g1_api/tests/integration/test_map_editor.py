"""Map editor endpoints: grid read/write in place, per-map tour points,
and the external-executor action seam."""

from __future__ import annotations

from typing import Any

from tests.support.harness import build_map_package, upload_select_relocalize, wait_until


def _upload(client: Any, map_id: str) -> None:
    data = build_map_package(map_id)
    assert client.post(
        "/api/core/slam/v1/maps", content=data,
        headers={"Content-Type": "application/octet-stream"},
    ).status_code == 200


def test_grid_read_edit_write_roundtrip(client: Any) -> None:
    _upload(client, "edit-me")

    resp = client.get("/api/core/slam/v1/maps/edit-me/grid")
    assert resp.status_code == 200
    original = resp.content
    assert original.startswith(b"P5")

    # paint the single cell black, save under the same map_id
    edited = original[: original.rindex(b"\x00")] + b"\x00" if original.endswith(b"\x00") else original
    edited = original[:-1] + b"\x00"
    resp = client.put(
        "/api/core/slam/v1/maps/edit-me/grid", content=edited,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["saved"] is True

    # read-back returns the edited bytes; the package still unpacks cleanly
    assert client.get("/api/core/slam/v1/maps/edit-me/grid").content == edited
    pkg = client.get("/api/core/slam/v1/maps/edit-me/package")
    assert pkg.status_code == 200
    from g1_api.models.map import unpack_map_package

    unpacked = unpack_map_package(pkg.content)
    assert unpacked.files["grid.pgm"] == edited


def test_grid_write_rejects_dimension_change(client: Any) -> None:
    _upload(client, "edit-dims")
    bad = b"P5\n2 2\n255\n\x00\x00\x00\x00"  # library map is 1x1
    resp = client.put(
        "/api/core/slam/v1/maps/edit-dims/grid", content=bad,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 400
    resp = client.put(
        "/api/core/slam/v1/maps/edit-dims/grid", content=b"not a pgm",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 400


def test_tour_points_write_with_executor_field(client: Any) -> None:
    _upload(client, "edit-points")
    resp = client.put(
        "/api/core/slam/v1/maps/edit-points/tour-points",
        json={"points": [
            {"name": "P1", "x": 0.4, "y": 0.0, "yaw": 0.0,
             "actions": [{"executor": "onboard", "type": "arm", "id": 26}],
             "tts_text": "你好", "dwell_s": 0.0,
             "reach_threshold": 0.3, "yaw_threshold": 0.5},
            {"name": "P2", "x": 0.8, "y": 0.0, "yaw": 0.0,
             "actions": [{"executor": "external", "type": "my-action", "params": {"speed": 1}}],
             "tts_text": "", "dwell_s": 0.0},
        ]},
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["map"]["tour_points"]
    assert saved[0]["reach_threshold"] == 0.3
    assert saved[0]["yaw_threshold"] == 0.5
    assert saved[1]["yaw_threshold"] == 0.5  # default fills in
    assert saved[0]["actions"][0]["executor"] == "onboard"
    assert saved[1]["actions"][0] == {"executor": "external", "type": "my-action", "params": {"speed": 1}}


def test_tour_runs_external_action_as_noop_when_unconfigured(client: Any, core: Any) -> None:
    """An external action with no executor configured is logged and skipped;
    the point still succeeds and the onboard pipeline is untouched."""
    upload_select_relocalize(client, "ext-tour")
    client.put(
        "/api/core/slam/v1/maps/ext-tour/tour-points",
        json={"points": [
            {"name": "E1", "x": 0.3, "y": 0.0, "yaw": 0.0, "reach_threshold": 0.2,
             "actions": [{"executor": "external", "type": "wave-flag"}], "tts_text": "", "dwell_s": 0.0},
        ]},
    )
    arm_log_before = list(core.adapter.world.arm_log)
    assert client.post("/api/tour/v1/tours/:start").status_code == 200
    wait_until(
        lambda: client.get("/api/tour/v1/tours/:current").json()["status"] in ("finished", "failed")
        and client.get("/api/tour/v1/tours/:current").json(),
        description="tour with external action to end",
        timeout_s=30.0,
    )
    final = client.get("/api/tour/v1/tours/:current").json()
    assert final["status"] == "finished", final
    assert final["points"][0]["status"] == "succeeded"
    assert core.adapter.world.arm_log == arm_log_before  # onboard pipeline untouched
    events = client.get("/api/tour/v1/events").json()["events"]
    assert any(e.get("type") == "external_action_skipped" for e in events)
