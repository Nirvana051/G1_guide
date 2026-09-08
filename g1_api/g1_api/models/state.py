"""Robot state blocks and the named interlocks.

FAST-LIO localization has **no 0-100 quality score** on the G1; this model
deliberately has no quality field -- "initialized or not" is all that can be
truthfully reported, and the build prompt forbids inventing a quality number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from g1_api.models.geometry import Pose2D, Stamp

__all__ = [
    "Interlock",
    "HealthState",
    "SafetyStatus",
    "LocalizationStatus",
    "CapabilityInfo",
    "DeviceIdentity",
    "NAMES_BY_FLAG_INTERLOCK",
]


class Interlock(object):
    """Canonical names for the conditions that block a command."""

    #: Hardware / middleware liveness interlocks.
    ROSCORE_DOWN = "roscore_down"
    MOVE_BASE_DOWN = "move_base_down"
    VELOCITY_BRIDGE_DOWN = "velocity_bridge_down"
    DDS_UNREACHABLE = "dds_unreachable"
    #: Semantic preconditions.
    MAP_NOT_SELECTED = "map_not_selected"
    LOCALIZATION_NOT_INITIALIZED = "localization_not_initialized"
    #: A tour is running; manual motion is refused.
    TOUR_RUNNING = "tour_running"
    #: A mapping session is running; navigation / map switching are refused
    #: (the navigation stack is down while FAST-LIO maps). Teleop (MoveAction)
    #: stays allowed -- it is how the mapping run is driven.
    MAPPING_RUNNING = "mapping_running"
    #: An external replay motion owns the arms (rt/arm_sdk weight = 1); arm /
    #: navigation / tour commands are refused until it finishes.
    EXTERNAL_MOTION_RUNNING = "external_motion_running"
    #: Emergency stop latched.
    ESTOP_ENGAGED = "estop_engaged"
    HARDWARE_FAULT = "hardware_fault"
    ADAPTER_NOT_READY = "adapter_not_ready"


#: Config-flag interlock name per capability flag. ``motion_not_allowed`` is the
#: one the build prompt names explicitly; the rest follow the same scheme.
NAMES_BY_FLAG_INTERLOCK: Dict[str, str] = {
    "allow_motion": "motion_not_allowed",
    "allow_navigation": "navigation_not_allowed",
    "allow_map_write": "map_write_not_allowed",
    "allow_arm": "arm_not_allowed",
    "allow_hand": "hand_not_allowed",
    "allow_voice": "voice_not_allowed",
    "allow_tour": "tour_not_allowed",
}


@dataclass(frozen=True)
class HealthState:
    has_warning: bool = False
    has_error: bool = False
    has_fatal: bool = False
    base_error: Tuple[Dict[str, Any], ...] = ()
    interlocks: Tuple[str, ...] = ()
    stamp: Stamp = field(default_factory=Stamp.zero)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "hasWarning": self.has_warning,
            "hasError": self.has_error,
            "hasFatal": self.has_fatal,
            "baseError": [dict(e) for e in self.base_error],
            "interlocks": list(self.interlocks),
        }
        out.update(self.stamp.to_dict())
        return out

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "HealthState":
        return HealthState(
            has_warning=bool(data.get("hasWarning", False)),
            has_error=bool(data.get("hasError", False)),
            has_fatal=bool(data.get("hasFatal", False)),
            base_error=tuple(dict(e) for e in data.get("baseError", ()) or ()),
            interlocks=tuple(str(i) for i in data.get("interlocks", ()) or ()),
            stamp=Stamp.from_dict(data),
        )


@dataclass(frozen=True)
class SafetyStatus:
    """The safety block, incl. the four G1 middleware liveness flags."""

    estop_engaged: bool = False
    roscore_down: bool = False
    move_base_down: bool = False
    velocity_bridge_down: bool = False
    dds_unreachable: bool = False
    map_not_selected: bool = True
    localization_not_initialized: bool = True
    tour_running: bool = False
    mapping_running: bool = False
    active_interlocks: Tuple[str, ...] = ()
    stamp: Stamp = field(default_factory=Stamp.zero)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "estop_engaged": self.estop_engaged,
            "roscore_down": self.roscore_down,
            "move_base_down": self.move_base_down,
            "velocity_bridge_down": self.velocity_bridge_down,
            "dds_unreachable": self.dds_unreachable,
            "map_not_selected": self.map_not_selected,
            "localization_not_initialized": self.localization_not_initialized,
            "tour_running": self.tour_running,
            "mapping_running": self.mapping_running,
            "active_interlocks": list(self.active_interlocks),
        }
        out.update(self.stamp.to_dict())
        return out


@dataclass(frozen=True)
class LocalizationStatus:
    initialized: bool = False
    map_id: Optional[str] = None
    note: str = ""
    pose: Optional[Pose2D] = None
    stamp: Stamp = field(default_factory=Stamp.zero)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "initialized": self.initialized,
            "map_id": self.map_id,
            "note": self.note,
        }
        if self.pose is not None:
            out["pose"] = self.pose.to_dict()
        out.update(self.stamp.to_dict())
        return out

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "LocalizationStatus":
        return LocalizationStatus(
            initialized=bool(data.get("initialized", False)),
            map_id=data.get("map_id"),
            note=str(data.get("note", "")),
            pose=Pose2D.from_dict(data["pose"]) if data.get("pose") else None,
            stamp=Stamp.from_dict(data),
        )


@dataclass(frozen=True)
class CapabilityInfo:
    name: str = ""
    ready: bool = False
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ready": self.ready,
            "description": self.description,
        }


@dataclass(frozen=True)
class DeviceIdentity:
    manufacturer_name: str = "Unitree"
    model_name: str = "G1"
    software_version: str = "0.1.0"
    device_id: str = ""
    mode: str = "mock"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manufacturer": self.manufacturer_name,
            "model": self.model_name,
            "softwareVersion": self.software_version,
            "deviceId": self.device_id,
            "mode": self.mode,
        }


def collect_interlocks(
    estop: bool = False,
    roscore_down: bool = False,
    move_base_down: bool = False,
    velocity_bridge_down: bool = False,
    dds_unreachable: bool = False,
    map_not_selected: bool = True,
    localization_not_initialized: bool = True,
    tour_running: bool = False,
    mapping_running: bool = False,
    hardware_fault: bool = False,
    adapter_ready: bool = True,
) -> Tuple[str, ...]:
    """Assemble the named interlocks from raw booleans, in a stable order."""
    found: List[str] = []
    if not adapter_ready:
        found.append(Interlock.ADAPTER_NOT_READY)
    if estop:
        found.append(Interlock.ESTOP_ENGAGED)
    if hardware_fault:
        found.append(Interlock.HARDWARE_FAULT)
    if roscore_down:
        found.append(Interlock.ROSCORE_DOWN)
    if move_base_down:
        found.append(Interlock.MOVE_BASE_DOWN)
    if velocity_bridge_down:
        found.append(Interlock.VELOCITY_BRIDGE_DOWN)
    if dds_unreachable:
        found.append(Interlock.DDS_UNREACHABLE)
    if map_not_selected:
        found.append(Interlock.MAP_NOT_SELECTED)
    if localization_not_initialized:
        found.append(Interlock.LOCALIZATION_NOT_INITIALIZED)
    if tour_running:
        found.append(Interlock.TOUR_RUNNING)
    if mapping_running:
        found.append(Interlock.MAPPING_RUNNING)
    return tuple(dict.fromkeys(found))
