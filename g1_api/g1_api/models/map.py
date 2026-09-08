"""The ``.g1map`` atomic composite map package.

The underlying G1 map is four scattered files (``map.pcd`` FAST-LIO reference
cloud, ``ground_map.pcd``, ``mymap.pgm`` + ``mymap.yaml`` 2D grid). The
``.g1map`` format bundles them into one ``tar.gz`` with a ``manifest.json`` that
also carries the **tour points** -- the Slamtec idea of binding semantics and
grid into one atomic composite (their STCM), reproduced here.

Packaging is pure stdlib (``tarfile``/``gzip``/``hashlib``) and never touches
the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from g1_api.errors import ValidationError

__all__ = [
    "MAP_FILES",
    "TourPointModel",
    "MapManifest",
    "MapPackage",
    "MappingStatus",
    "pack_map_package",
    "unpack_map_package",
    "package_from_artifacts",
    "sha256_hex",
]

#: The four data files a map package contains, in canonical order.
MAP_FILES: Tuple[str, ...] = ("map.pcd", "ground_map.pcd", "grid.pgm", "grid.yaml")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class TourPointModel:
    """One guided-tour stop. Travels *inside* the map package manifest."""

    name: str = ""
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    #: list of ``{"type": "arm", "id": int}`` or ``{"type": "hand", "cmd": str}``
    actions: Tuple[Dict[str, Any], ...] = ()
    tts_text: str = ""
    dwell_s: float = 0.0
    reach_threshold: float = 0.35
    yaw_threshold: float = 0.5
    #: Manual TTS wait at the point (seconds). 0 = auto estimate
    #: (len(text) * per-speaker coefficient + margin).
    tts_duration_s: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", tuple(dict(a) for a in self.actions))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "yaw": self.yaw,
            "actions": [dict(a) for a in self.actions],
            "tts_text": self.tts_text,
            "dwell_s": self.dwell_s,
            "reach_threshold": self.reach_threshold,
            "yaw_threshold": self.yaw_threshold,
            "tts_duration_s": self.tts_duration_s,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "TourPointModel":
        return TourPointModel(
            name=str(data.get("name", "")),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            yaw=float(data.get("yaw", 0.0)),
            actions=tuple(dict(a) for a in data.get("actions", ()) or ()),
            tts_text=str(data.get("tts_text", "")),
            dwell_s=float(data.get("dwell_s", 0.0)),
            reach_threshold=float(data.get("reach_threshold", 0.35)),
            yaw_threshold=float(data.get("yaw_threshold", 0.5)),
            tts_duration_s=float(data.get("tts_duration_s", 0.0)),
        )


@dataclass(frozen=True)
class MapManifest:
    map_id: str = ""
    name: str = ""
    created_utc: float = 0.0
    resolution: float = 0.05
    origin: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    tour_points: Tuple[TourPointModel, ...] = ()
    checksums: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "map_id": self.map_id,
            "name": self.name,
            "created_utc": self.created_utc,
            "resolution": self.resolution,
            "origin": list(self.origin),
            "tour_points": [p.to_dict() for p in self.tour_points],
            "checksums": dict(self.checksums),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MapManifest":
        origin = data.get("origin", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)
        return MapManifest(
            map_id=str(data.get("map_id", "")),
            name=str(data.get("name", "")),
            created_utc=float(data.get("created_utc", 0.0)),
            resolution=float(data.get("resolution", 0.05)),
            origin=(
                float(origin[0]),
                float(origin[1]),
                float(origin[2]) if len(origin) > 2 else 0.0,
            ),
            tour_points=tuple(
                TourPointModel.from_dict(p) for p in data.get("tour_points", ()) or ()
            ),
            checksums={str(k): str(v) for k, v in (data.get("checksums") or {}).items()},
        )


@dataclass(frozen=True)
class MapPackage:
    manifest: MapManifest
    files: Dict[str, bytes] = field(default_factory=dict)

    def file_checksums(self) -> Dict[str, str]:
        return {name: sha256_hex(self.files[name]) for name in MAP_FILES if name in self.files}


def _build_tar(manifest: MapManifest, files: Dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

        for name in MAP_FILES:
            data = files.get(name)
            if data is None:
                continue
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def pack_map_package(
    manifest: MapManifest, files: Dict[str, bytes]
) -> bytes:
    """Bundle a manifest and the four map files into a ``.g1map`` (tar.gz).

    The manifest's ``checksums`` are recomputed from the provided files and
    stored, so a package is always self-consistent.
    """
    resolved = MapManifest(
        map_id=manifest.map_id,
        name=manifest.name,
        created_utc=manifest.created_utc,
        resolution=manifest.resolution,
        origin=manifest.origin,
        tour_points=manifest.tour_points,
        checksums={name: sha256_hex(files[name]) for name in MAP_FILES if name in files},
    )
    return _build_tar(resolved, files)


def unpack_map_package(data: bytes) -> MapPackage:
    """Parse a ``.g1map`` (tar.gz) into a :class:`MapPackage`.

    :raises ValidationError: on a non-tar stream, a missing/overlarge manifest,
        or a checksum mismatch.
    """
    try:
        buffer = io.BytesIO(data)
        with tarfile.open(fileobj=buffer, mode="r:gz") as tar:
            members = {m.name: m for m in tar.getmembers() if m.isfile()}
            manifest_member = members.get("manifest.json")
            if manifest_member is None:
                raise ValidationError(
                    "map package is missing manifest.json", field="map_package",
                    source="models.map",
                )
            if manifest_member.size > 10 * 1024 * 1024:
                raise ValidationError(
                    "manifest.json is too large", field="manifest.json", source="models.map"
                )
            manifest_raw = tar.extractfile(manifest_member).read()
            manifest = MapManifest.from_dict(json.loads(manifest_raw.decode("utf-8")))

            files: Dict[str, bytes] = {}
            for name in MAP_FILES:
                member = members.get(name)
                if member is not None:
                    files[name] = tar.extractfile(member).read()
    except ValidationError:
        raise
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise ValidationError(
            "not a valid .g1map package: %s" % (exc,),
            field="map_package",
            source="models.map",
        )

    # Checksum verification.
    for name, expected in manifest.checksums.items():
        if name not in files:
            continue
        actual = sha256_hex(files[name])
        if actual != expected:
            raise ValidationError(
                "checksum mismatch for %s (expected %s, got %s)" % (name, expected, actual),
                field="checksums.%s" % name,
                source="models.map",
            )

    if "grid.yaml" in files:
        _normalize_grid_yaml(files)

    return MapPackage(manifest=manifest, files=files)


def _normalize_grid_yaml(files: Dict[str, bytes]) -> None:
    """Ensure the grid.yaml ``image`` field is the relative name and any
    ``nan``/``-nan`` is rewritten to ``0.0``. NB ``0.0``, not ``0``: ``-nan``
    would otherwise become ``-0``, which yaml-cpp's as<double>() rejects and
    map_server dies on (observed in simulation)."""
    import re

    try:
        text = files["grid.yaml"].decode("utf-8")
    except UnicodeDecodeError:
        return
    fixed = re.sub(r"image:\s*\S+", "image: grid.pgm", text)
    fixed = re.sub(r"(?i)-?\bnan\b", "0.0", fixed)
    if fixed != text:
        files["grid.yaml"] = fixed.encode("utf-8")


@dataclass(frozen=True)
class MappingStatus:
    """State of the mapping (map-recording) session."""

    active: bool = False
    started_utc: Optional[float] = None
    elapsed_s: float = 0.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "started_utc": self.started_utc,
            "elapsed_s": round(self.elapsed_s, 1),
            "note": self.note,
        }


#: Filenames a finished mapping run must yield (map_saver naming on the left).
_ARTIFACT_SOURCES = {
    "map.pcd": ("map.pcd",),
    "ground_map.pcd": ("ground_map.pcd",),
    "grid.pgm": ("mymap.pgm", "grid.pgm"),
    "grid.yaml": ("mymap.yaml", "grid.yaml"),
}


def package_from_artifacts(
    src_dir: str,
    map_id: str,
    name: str = "",
    created_utc: float = 0.0,
    tour_points: Tuple[Any, ...] = (),
) -> MapPackage:
    """Build a MapPackage from raw mapping artifacts on disk.

    Accepts both the course map_saver naming (``mymap.pgm``/``mymap.yaml``)
    and the package naming (``grid.pgm``/``grid.yaml``); normalizes grid.yaml.
    """
    import os
    import re

    files: Dict[str, bytes] = {}
    for target, candidates in _ARTIFACT_SOURCES.items():
        for candidate in candidates:
            path = os.path.join(src_dir, candidate)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    files[target] = handle.read()
                break
        if target not in files:
            raise ValidationError(
                "mapping artifact missing: none of %s found in %s"
                % ("/".join(candidates), src_dir),
                field=target,
                source="models.map",
            )
    _normalize_grid_yaml(files)

    yaml_text = files["grid.yaml"].decode("utf-8", errors="replace")
    resolution, origin = 0.05, (0.0, 0.0, 0.0)
    match = re.search(r"resolution:\s*([0-9.]+)", yaml_text)
    if match:
        resolution = float(match.group(1))
    match = re.search(r"origin:\s*\[([^\]]+)\]", yaml_text)
    if match:
        parts = [float(p) for p in match.group(1).split(",")]
        if len(parts) >= 3:
            origin = (parts[0], parts[1], parts[2])

    manifest = MapManifest(
        map_id=map_id,
        name=name or map_id,
        created_utc=created_utc,
        resolution=resolution,
        origin=origin,
        tour_points=tuple(tour_points),
    )
    return MapPackage(manifest=manifest, files=files)
