"""The ``.g1map`` package: pack/unpack round-trip and checksum verification."""

from __future__ import annotations

import time

import pytest

from g1_api.errors import ValidationError
from g1_api.models.map import (
    MapManifest,
    TourPointModel,
    pack_map_package,
    sha256_hex,
    unpack_map_package,
)


def _manifest() -> MapManifest:
    points = (
        TourPointModel(name="点1", x=1.0, y=2.0, yaw=0.5, actions=[{"type": "arm", "id": 26}], tts_text="第一站"),
        TourPointModel(name="点2", x=3.0, y=4.0, yaw=-0.5, actions=[{"type": "hand", "cmd": "7"}], tts_text="第二站"),
    )
    return MapManifest(
        map_id="map-123", name="test map", created_utc=time.time(), resolution=0.05,
        origin=(0.0, 0.0, 0.0), tour_points=points,
    )


def _files() -> dict:
    return {
        "map.pcd": b"FAST-LIO reference cloud",
        "ground_map.pcd": b"ground cloud",
        "grid.pgm": b"P5\n1 1\n255\n\x00",
        "grid.yaml": b"image: mymap.pgm\nresolution: 0.05\norigin: [-15.15, -21.9, 0.0]\n",
    }


def test_round_trip_preserves_manifest_and_files() -> None:
    manifest = _manifest()
    files = _files()
    data = pack_map_package(manifest, files)
    package = unpack_map_package(data)
    assert package.manifest.map_id == manifest.map_id
    assert package.manifest.tour_points == manifest.tour_points
    assert package.files["map.pcd"] == files["map.pcd"]
    assert package.files["grid.pgm"] == files["grid.pgm"]


def test_checksums_are_recomputed_on_pack() -> None:
    manifest = _manifest()
    data = pack_map_package(manifest, _files())
    package = unpack_map_package(data)
    assert package.manifest.checksums["map.pcd"] == sha256_hex(_files()["map.pcd"])


def test_checksum_mismatch_is_rejected() -> None:
    manifest = _manifest()
    manifest = MapManifest(
        map_id=manifest.map_id, name=manifest.name, created_utc=manifest.created_utc,
        resolution=manifest.resolution, origin=manifest.origin,
        tour_points=manifest.tour_points, checksums={"map.pcd": "deadbeef" * 8},
    )
    import g1_api.models.map as m

    data = m._build_tar(manifest, _files())
    with pytest.raises(ValidationError):
        unpack_map_package(data)


def test_missing_manifest_is_rejected() -> None:
    import g1_api.models.map as m

    data = m._build_tar(_manifest(), _files())
    # A package without manifest.json: build a bare tar
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="map.pcd")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    with pytest.raises(ValidationError):
        unpack_map_package(buf.getvalue())


def test_garbage_input_is_rejected() -> None:
    with pytest.raises(ValidationError):
        unpack_map_package(b"this is not a tar file")
