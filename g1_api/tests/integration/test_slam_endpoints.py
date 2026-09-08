"""Map package endpoints and localization."""

from __future__ import annotations

from typing import Any

from tests.support.harness import build_map_package, upload_select_relocalize


def test_upload_list_select_current(client: Any) -> None:
    resp = client.post(
        "/api/core/slam/v1/maps",
        content=build_map_package("map-slam-1"),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["map"]["map_id"] == "map-slam-1"

    maps = client.get("/api/core/slam/v1/maps").json()["maps"]
    assert any(m["map_id"] == "map-slam-1" for m in maps)

    # Not selected yet.
    current = client.get("/api/core/slam/v1/maps/:current").json()
    assert current["selected"] is False

    # Select.
    resp = client.post("/api/core/slam/v1/maps/map-slam-1/:select")
    assert resp.status_code == 200
    assert resp.json()["selected"] is True
    assert resp.json()["requires_relocalization"] is True

    current = client.get("/api/core/slam/v1/maps/:current").json()
    assert current["selected"] is True
    assert current["map"]["map_id"] == "map-slam-1"


def test_download_round_trips(client: Any) -> None:
    upload_select_relocalize(client, "map-dl")
    resp = client.get("/api/core/slam/v1/maps/map-dl/package")
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x1f\x8b")  # gzip magic


def test_delete_current_map_is_rejected(client: Any) -> None:
    upload_select_relocalize(client, "map-del-current")
    resp = client.delete("/api/core/slam/v1/maps/map-del-current")
    assert resp.status_code == 409, resp.text


def test_delete_non_current_map(client: Any) -> None:
    client.post(
        "/api/core/slam/v1/maps",
        content=build_map_package("map-del-other"),
        headers={"Content-Type": "application/octet-stream"},
    )
    resp = client.delete("/api/core/slam/v1/maps/map-del-other")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_localization_status_reports_initialized(client: Any) -> None:
    upload_select_relocalize(client, "map-loc")
    status = client.get("/api/core/slam/v1/localization/status").json()
    assert status["initialized"] is True
    assert status["map_id"] == "map-loc"
    assert "quality" not in status  # FAST-LIO has no 0-100 quality score


def test_relocalize_requires_a_selected_map() -> None:
    # No map selected: relocalize must be refused with map_not_selected.
    from tests.support.harness import build_config, make_client

    with make_client(build_config(allow_all=True)) as fresh:
        resp = fresh.post("/api/core/slam/v1/localization/:relocalize", json={"x": 0, "y": 0, "yaw": 0})
        assert resp.status_code == 409, resp.text
        assert "map_not_selected" in resp.json().get("details", {}).get("active_interlocks", [])
