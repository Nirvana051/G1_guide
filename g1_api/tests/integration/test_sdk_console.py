"""The SDK debug console: page, meta, assets, and OpenAPI exclusion."""

from __future__ import annotations

from typing import Any


def test_console_page_serves_html(client: Any) -> None:
    resp = client.get("/sdk")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "G1 SDK" in resp.text


def test_meta_lists_all_groups_and_endpoints(client: Any) -> None:
    meta = client.get("/sdk/meta.json").json()
    group_ids = [g["id"] for g in meta["groups"]]
    assert group_ids == [
        "motion", "mapping", "map", "localization", "voice", "arm", "extact",
        "hand", "system", "tour",
    ]
    assert len(meta["endpoints"]) >= 30
    # Every endpoint has a Chinese description, params and a safety badge.
    for ep in meta["endpoints"]:
        assert ep["title"]
        assert ep["description"]
        assert ep["safety"] in ("SAFE_READ", "ACTION", "SAFETY_CRITICAL")


def test_console_assets_are_served(client: Any) -> None:
    assert client.get("/sdk/static/js/app.js").status_code == 200
    assert client.get("/sdk/static/css/sdk.css").status_code == 200


def test_console_asset_path_traversal_is_blocked(client: Any) -> None:
    resp = client.get("/sdk/static/../../../etc/passwd")
    assert resp.status_code == 404


def test_console_routes_are_not_in_openapi(client: Any, app: Any) -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert not any(p.startswith("/sdk") for p in paths)
