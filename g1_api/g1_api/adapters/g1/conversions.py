"""Pure-math conversions between yaw and quaternion (REP-103 convention)."""

from __future__ import annotations

import math
from typing import Tuple

__all__ = ["yaw_from_quaternion_xyzw", "quaternion_from_yaw", "normalize_angle"]


def normalize_angle(angle: float) -> float:
    wrapped = math.fmod(float(angle) + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


def yaw_from_quaternion_xyzw(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def quaternion_from_yaw(yaw: float) -> Tuple[float, float, float, float]:
    """Return (x, y, z, w) for a Z-axis-only rotation by ``yaw``."""
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))
