"""Geometry primitives: units and frames in the field names (REP-103 convention).

Linear in metres, angular in radians (CCW positive), velocities in m/s and
rad/s. Every pose carries its frame id.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "Stamp",
    "Point2D",
    "Pose2D",
    "Pose3D",
    "Twist",
    "normalize_angle_rad",
    "angle_difference_rad",
]

_TAU = 2.0 * math.pi


def normalize_angle_rad(angle_rad: float) -> float:
    """Wrap an angle to ``(-pi, pi]``."""
    wrapped = math.fmod(float(angle_rad) + math.pi, _TAU)
    if wrapped <= 0.0:
        wrapped += _TAU
    return wrapped - math.pi


def angle_difference_rad(a_rad: float, b_rad: float) -> float:
    """Signed smallest rotation from ``b_rad`` to ``a_rad``, in ``(-pi, pi]``."""
    return normalize_angle_rad(float(a_rad) - float(b_rad))


@dataclass(frozen=True)
class Stamp:
    mono_ns: int = 0
    utc_ns: int = 0

    @staticmethod
    def now() -> "Stamp":
        return Stamp(mono_ns=time.monotonic_ns(), utc_ns=time.time_ns())

    @staticmethod
    def zero() -> "Stamp":
        return Stamp(0, 0)

    @property
    def is_set(self) -> bool:
        return bool(self.mono_ns or self.utc_ns)

    def to_dict(self) -> Dict[str, Any]:
        return {"t_mono_ns": self.mono_ns, "t_utc_ns": self.utc_ns}

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> "Stamp":
        if not data:
            return Stamp.zero()
        return Stamp(
            mono_ns=int(data.get("t_mono_ns", data.get("mono_ns", 0)) or 0),
            utc_ns=int(data.get("t_utc_ns", data.get("utc_ns", 0)) or 0),
        )


@dataclass(frozen=True)
class Point2D:
    x_m: float = 0.0
    y_m: float = 0.0

    def distance_to(self, other: "Point2D") -> float:
        return math.hypot(self.x_m - other.x_m, self.y_m - other.y_m)

    def to_dict(self) -> Dict[str, Any]:
        return {"x_m": self.x_m, "y_m": self.y_m}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Point2D":
        return Point2D(x_m=float(data.get("x_m", 0.0)), y_m=float(data.get("y_m", 0.0)))

    @staticmethod
    def from_pair(pair: Tuple[float, float]) -> "Point2D":
        return Point2D(x_m=float(pair[0]), y_m=float(pair[1]))


@dataclass(frozen=True)
class Pose2D:
    x_m: float = 0.0
    y_m: float = 0.0
    yaw_rad: float = 0.0
    frame_id: str = "map"

    def to_point2d(self) -> Point2D:
        return Point2D(self.x_m, self.y_m)

    def distance_to(self, other: "Pose2D") -> float:
        return math.hypot(self.x_m - other.x_m, self.y_m - other.y_m)

    def yaw_difference_to(self, other: "Pose2D") -> float:
        return angle_difference_rad(other.yaw_rad, self.yaw_rad)

    def is_close_to(
        self, other: "Pose2D", position_tol_m: float = 0.2, yaw_tol_rad: float = 0.15
    ) -> bool:
        return (
            self.distance_to(other) <= position_tol_m
            and abs(self.yaw_difference_to(other)) <= yaw_tol_rad
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x_m": self.x_m,
            "y_m": self.y_m,
            "yaw_rad": self.yaw_rad,
            "frame_id": self.frame_id,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Pose2D":
        return Pose2D(
            x_m=float(data.get("x_m", 0.0)),
            y_m=float(data.get("y_m", 0.0)),
            yaw_rad=float(data.get("yaw_rad", 0.0)),
            frame_id=str(data.get("frame_id", "map")),
        )


@dataclass(frozen=True)
class Pose3D:
    x_m: float = 0.0
    y_m: float = 0.0
    z_m: float = 0.0
    yaw_rad: float = 0.0
    pitch_rad: float = 0.0
    roll_rad: float = 0.0
    frame_id: str = "map"

    def to_pose2d(self) -> Pose2D:
        return Pose2D(self.x_m, self.y_m, self.yaw_rad, self.frame_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x_m": self.x_m,
            "y_m": self.y_m,
            "z_m": self.z_m,
            "yaw_rad": self.yaw_rad,
            "pitch_rad": self.pitch_rad,
            "roll_rad": self.roll_rad,
            "frame_id": self.frame_id,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Pose3D":
        return Pose3D(
            x_m=float(data.get("x_m", 0.0)),
            y_m=float(data.get("y_m", 0.0)),
            z_m=float(data.get("z_m", 0.0)),
            yaw_rad=float(data.get("yaw_rad", 0.0)),
            pitch_rad=float(data.get("pitch_rad", 0.0)),
            roll_rad=float(data.get("roll_rad", 0.0)),
            frame_id=str(data.get("frame_id", "map")),
        )


@dataclass(frozen=True)
class Twist:
    vx_mps: float = 0.0
    vy_mps: float = 0.0
    omega_radps: float = 0.0
    frame_id: str = "body"

    @property
    def is_stopped(self) -> bool:
        return (
            abs(self.vx_mps) < 1e-9
            and abs(self.vy_mps) < 1e-9
            and abs(self.omega_radps) < 1e-9
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vx_mps": self.vx_mps,
            "vy_mps": self.vy_mps,
            "omega_radps": self.omega_radps,
            "frame_id": self.frame_id,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Twist":
        return Twist(
            vx_mps=float(data.get("vx_mps", 0.0)),
            vy_mps=float(data.get("vy_mps", 0.0)),
            omega_radps=float(data.get("omega_radps", 0.0)),
            frame_id=str(data.get("frame_id", "body")),
        )
