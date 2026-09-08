"""Tour data model. The tour layer sits *above* the core action API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from g1_api.errors import ValidationError

__all__ = [
    "TourPoint",
    "Tour",
    "TourPointResult",
    "TourState",
    "ON_FAILURE_OPTIONS",
]

ON_FAILURE_OPTIONS: Tuple[str, ...] = ("retry_once", "skip", "abort")


@dataclass(frozen=True)
class TourPoint:
    name: str = ""
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    #: ``{"type": "arm", "id": int}`` or ``{"type": "hand", "cmd": str}``
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
        for action in self.actions:
            if action.get("type") == "arm" and "id" not in action:
                raise ValidationError(
                    "arm action requires an integer 'id'", field="actions", source="models.tour"
                )
            if action.get("type") == "hand" and "cmd" not in action:
                raise ValidationError(
                    "hand action requires a string 'cmd'", field="actions", source="models.tour"
                )

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
    def from_dict(data: Dict[str, Any]) -> "TourPoint":
        return TourPoint(
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
class Tour:
    tour_id: str = ""
    map_id: str = ""
    points: Tuple[TourPoint, ...] = ()
    loop: bool = False
    on_failure: str = "retry_once"
    #: Runtime-only (never persisted): skip the MoveTo leg of every point --
    #: used by the in-place single-point rehearsal, which therefore needs no
    #: navigation stack readiness and no localization.
    skip_nav: bool = False

    def __post_init__(self) -> None:
        if self.on_failure not in ON_FAILURE_OPTIONS:
            raise ValidationError(
                "on_failure must be one of %s" % (", ".join(ON_FAILURE_OPTIONS),),
                field="on_failure",
                source="models.tour",
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tour_id": self.tour_id,
            "map_id": self.map_id,
            "points": [p.to_dict() for p in self.points],
            "loop": self.loop,
            "on_failure": self.on_failure,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Tour":
        return Tour(
            tour_id=str(data.get("tour_id", "")),
            map_id=str(data.get("map_id", "")),
            points=tuple(TourPoint.from_dict(p) for p in data.get("points", ()) or ()),
            loop=bool(data.get("loop", False)),
            on_failure=str(data.get("on_failure", "retry_once")),
        )


@dataclass(frozen=True)
class TourPointResult:
    index: int = 0
    name: str = ""
    status: str = "pending"  # pending | running | succeeded | failed | skipped
    attempts: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "status": self.status,
            "attempts": self.attempts,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TourState:
    status: str = "idle"  # idle | running | paused | finished | stopped | failed
    tour_id: str = ""
    map_id: str = ""
    current_index: int = 0
    point_results: Tuple[TourPointResult, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "tour_id": self.tour_id,
            "map_id": self.map_id,
            "current_point_index": self.current_index,
            "points": [p.to_dict() for p in self.point_results],
        }
