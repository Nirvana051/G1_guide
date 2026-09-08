"""Per-speaker TTS coefficients, manual per-point TTS duration, and the
single-point full-pipeline rehearsal endpoint."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.harness import upload_select_relocalize, wait_until


def test_tts_estimate_uses_per_speaker_coefficient(client: Any, core: Any) -> None:
    text = "Hello from the showroom"  # 23 chars
    cn = client.post("/api/core/voice/v1/tts", json={"text": text, "speaker_id": 0}).json()
    en = client.post("/api/core/voice/v1/tts", json={"text": text, "speaker_id": 1}).json()
    cfg = core.config.voice
    assert cn["est_duration_s"] == pytest.approx(len(text) * cfg.char_duration_s)
    assert en["est_duration_s"] == pytest.approx(len(text) * cfg.char_duration_s_en)
    assert en["est_duration_s"] < cn["est_duration_s"]


def test_tts_duration_field_roundtrip(client: Any) -> None:
    upload_select_relocalize(client, "ttsdur-map")
    resp = client.put(
        "/api/core/slam/v1/maps/ttsdur-map/tour-points",
        json={"points": [
            {"name": "A", "x": 0.3, "y": 0.0, "yaw": 0.0, "tts_text": "hi",
             "tts_duration_s": 4.5, "dwell_s": 0.0},
            {"name": "B", "x": 0.6, "y": 0.0, "yaw": 0.0, "tts_text": "", "dwell_s": 0.0},
        ]},
    )
    saved = resp.json()["map"]["tour_points"]
    assert saved[0]["tts_duration_s"] == 4.5
    assert saved[1]["tts_duration_s"] == 0.0  # default = auto estimate


def _rehearse_and_wait(client: Any, index: int, query: str = "") -> Any:
    resp = client.post("/api/tour/v1/points/%d/:test%s" % (index, query))
    assert resp.status_code == 200, resp.text
    wait_until(
        lambda: client.get("/api/tour/v1/tours/:current").json()["status"]
        in ("finished", "failed"),
        description="single-point rehearsal to end", timeout_s=30.0,
    )
    return client.get("/api/tour/v1/tours/:current").json()


def test_single_point_rehearsal_default_is_in_place(client: Any) -> None:
    """Default rehearsal must NOT walk: actions + TTS only, robot stays put."""
    upload_select_relocalize(client, "rehearse-map")
    client.put(
        "/api/core/slam/v1/maps/rehearse-map/tour-points",
        json={"points": [
            {"name": "P0", "x": 0.2, "y": 0.0, "yaw": 0.0, "tts_text": "", "dwell_s": 0.0},
            {"name": "P1", "x": 2.5, "y": 1.5, "yaw": 1.0, "tts_text": "test",
             "actions": [{"executor": "onboard", "type": "arm", "id": 26}], "dwell_s": 0.0},
        ]},
    )
    # out-of-range index -> 404
    assert client.post("/api/tour/v1/points/5/:test").status_code == 404

    before = client.get("/api/core/slam/v1/localization/pose").json()
    final = _rehearse_and_wait(client, 1)
    assert final["status"] == "finished", final
    assert len(final["points"]) == 1 and final["points"][0]["name"] == "P1"
    assert final["points"][0]["status"] == "succeeded"
    after = client.get("/api/core/slam/v1/localization/pose").json()
    assert abs(after["x"] - before["x"]) < 0.15   # did not walk to (2.5, 1.5)
    assert abs(after["y"] - before["y"]) < 0.15


def test_multi_arm_actions_release_only_once(client: Any, core: Any) -> None:
    """Chained arm actions in one point: release(99) fires ONCE, after the
    last arm action -- not between them."""
    world = getattr(core.adapter, "world", None)
    if world is None:
        pytest.skip("mock only")
    upload_select_relocalize(client, "multiarm-map")
    client.put(
        "/api/core/slam/v1/maps/multiarm-map/tour-points",
        json={"points": [
            {"name": "M1", "x": 0.3, "y": 0.0, "yaw": 0.0, "tts_text": "", "dwell_s": 0.0,
             "actions": [
                 {"executor": "onboard", "type": "arm", "id": 26},
                 {"executor": "onboard", "type": "arm", "id": 32},
             ]},
        ]},
    )
    before = len(world.arm_log)
    final = _rehearse_and_wait(client, 0)
    assert final["status"] == "finished", final
    executed = list(world.arm_log[before:])
    assert executed == [26, 32, 99], executed  # one release, at the end


def test_in_place_rehearsal_needs_no_localization(client: Any) -> None:
    """Map selected but NOT relocalized: in-place rehearsal (actions+TTS only)
    must run; the walking variant must still be refused by the nav gate."""
    from tests.support.harness import build_map_package

    data = build_map_package("noloc-map")
    client.post("/api/core/slam/v1/maps", content=data,
                headers={"Content-Type": "application/octet-stream"})
    client.post("/api/core/slam/v1/maps/noloc-map/:select")
    client.put(
        "/api/core/slam/v1/maps/noloc-map/tour-points",
        json={"points": [
            {"name": "N1", "x": 1.0, "y": 1.0, "yaw": 0.0, "tts_text": "hi",
             "actions": [{"executor": "onboard", "type": "arm", "id": 26}], "dwell_s": 0.0},
        ]},
    )
    # walking variant: refused (localization_not_initialized)
    assert client.post("/api/tour/v1/points/0/:test?nav=1").status_code == 409
    # in-place: runs fine without localization
    final = _rehearse_and_wait(client, 0)
    assert final["status"] == "finished", final
    assert final["points"][0]["status"] == "succeeded"


def test_single_point_rehearsal_nav_1_walks_there(client: Any) -> None:
    upload_select_relocalize(client, "rehearse-nav-map")
    client.put(
        "/api/core/slam/v1/maps/rehearse-nav-map/tour-points",
        json={"points": [
            {"name": "W0", "x": 0.5, "y": 0.0, "yaw": 0.0, "tts_text": "", "dwell_s": 0.0},
        ]},
    )
    final = _rehearse_and_wait(client, 0, "?nav=1")
    assert final["status"] == "finished", final
    after = client.get("/api/core/slam/v1/localization/pose").json()
    assert abs(after["x"] - 0.5) < 0.4            # actually walked to the point
