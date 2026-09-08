"""External (replay) action library: metadata and .npy validation.

An external action is a recorded dual-arm joint trajectory (``.npy`` of shape
``(N, >=14)``) played back on the robot by the user's replay executor
(``g1_replay_min``). This module owns the on-disk library layout and the
minimal .npy header parsing needed to validate uploads -- stdlib only, so the
models layer stays dependency-free.

Library layout (``tour.external_actions_dir``)::

    <dir>/<action_id>.npy    the trajectory
    <dir>/<action_id>.json   the metadata below
"""

from __future__ import annotations

import ast
import json
import os
import re
import struct
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "ARM_DOF",
    "ExternalActionMeta",
    "build_execute_payload",
    "parse_npy_header",
    "sanitize_action_id",
    "library_list",
    "library_load",
    "library_paths",
]

ARM_DOF = 14

_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_action_id(name: str) -> str:
    """File-system-safe id from a user-provided name (mirrors map ids)."""
    cleaned = _ID_RE.sub("-", str(name).strip()).strip("-.")
    if not cleaned:
        raise ValueError("action name %r reduces to an empty id" % (name,))
    return cleaned


def parse_npy_header(data: bytes) -> Tuple[Tuple[int, ...], str, bool]:
    """Return ``(shape, dtype_str, fortran_order)`` from a .npy buffer.

    Supports format versions 1.0 and 2.0 (what numpy.save writes). Raises
    ``ValueError`` on anything that is not a well-formed .npy file.
    """
    if len(data) < 10 or data[:6] != b"\x93NUMPY":
        raise ValueError("not a .npy file (bad magic)")
    major = data[6]
    if major == 1:
        (hlen,) = struct.unpack("<H", data[8:10])
        header = data[10:10 + hlen]
    elif major == 2:
        (hlen,) = struct.unpack("<I", data[8:12])
        header = data[12:12 + hlen]
    else:
        raise ValueError("unsupported .npy version %d" % major)
    try:
        info = ast.literal_eval(header.decode("latin1").strip())
        shape = tuple(int(x) for x in info["shape"])
        dtype = str(info["descr"])
        fortran = bool(info["fortran_order"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError("malformed .npy header: %s" % exc)
    return shape, dtype, fortran


@dataclass(frozen=True)
class ExternalActionMeta:
    action_id: str
    name: str
    frames: int
    dims: int
    #: Playback frequency (Hz); should match the recording frequency.
    frequency_hz: float = 30.0
    #: Joint velocity limit forwarded to the replayer (rad/s).
    velocity_limit: float = 20.0
    size_bytes: int = 0
    created_unix: float = 0.0
    description: str = ""

    @property
    def duration_s(self) -> float:
        return self.frames / self.frequency_hz if self.frequency_hz > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["duration_s"] = round(self.duration_s, 2)
        return out

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ExternalActionMeta":
        return ExternalActionMeta(
            action_id=str(data["action_id"]),
            name=str(data.get("name", data["action_id"])),
            frames=int(data.get("frames", 0)),
            dims=int(data.get("dims", 0)),
            frequency_hz=float(data.get("frequency_hz", 30.0)),
            velocity_limit=float(data.get("velocity_limit", 20.0)),
            size_bytes=int(data.get("size_bytes", 0)),
            created_unix=float(data.get("created_unix", 0.0)),
            description=str(data.get("description", "")),
        )


def build_execute_payload(meta: "ExternalActionMeta", directory: str) -> Dict[str, Any]:
    """The JSON action contract sent to the replay executor (g1_replay_min's
    action server): the library entry resolved to an absolute file path plus
    playback parameters, so the server needs no library knowledge of its own."""
    npy_path, _ = library_paths(directory, meta.action_id)
    return {
        "executor": "external",
        "type": "replay",
        "id": meta.action_id,
        "file": os.path.abspath(npy_path),
        "frequency": meta.frequency_hz,
        "velocity_limit": meta.velocity_limit,
        "frames": meta.frames,
        "duration_s": meta.duration_s,
    }


def library_paths(directory: str, action_id: str) -> Tuple[str, str]:
    base = os.path.expanduser(directory)
    return (os.path.join(base, action_id + ".npy"),
            os.path.join(base, action_id + ".json"))


def library_load(directory: str, action_id: str) -> Optional[ExternalActionMeta]:
    _, meta_path = library_paths(directory, action_id)
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            return ExternalActionMeta.from_dict(json.load(handle))
    except Exception:  # noqa: BLE001 -- a broken entry must not break listing
        return None


def library_list(directory: str) -> List[ExternalActionMeta]:
    base = os.path.expanduser(directory)
    if not os.path.isdir(base):
        return []
    out: List[ExternalActionMeta] = []
    for fname in sorted(os.listdir(base)):
        if fname.endswith(".json"):
            meta = library_load(base, fname[:-5])
            if meta is not None:
                out.append(meta)
    return out
