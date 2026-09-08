"""Actions: the single unit of robot behaviour, and its typed parameters.

Everything the robot *does* goes through one submission endpoint with an
``action_name`` and an ``options`` payload. The G1 API implements exactly four
action factories (the build prompt's §4.2) and keeps the Slamtec asynchronous
command pattern: submission returns an ``action_id``; progress is polled.

``action_name`` accepts the G1 names (``g1.actions.MoveToAction``) *and* the
Slamtec originals (``slamtec.agent.actions.MoveToAction``) via a prefix map, so
an existing Slamtec-style client can point at this API unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple, Type

from g1_api.errors import FailureCode, ValidationError
from g1_api.models.enums import ActionKind, ActionLifecycle, ActionResult, ActionStage
from g1_api.models.geometry import Point2D, Stamp

__all__ = [
    "ACTION_NAME_BY_KIND",
    "KIND_BY_ACTION_NAME",
    "PARAMS_BY_KIND",
    "REQUIRED_SAFETY_FLAG_BY_KIND",
    "CANONICAL_ACTION_KINDS",
    "ActionParams",
    "MoveParams",
    "MoveToParams",
    "RotateParams",
    "ActionRequest",
    "ActionProgress",
    "ActionRecord",
    "params_from_dict",
]

_FACTORY_PREFIX = "g1.actions."

CANONICAL_ACTION_KINDS: Tuple[ActionKind, ...] = (
    ActionKind.MOVE,
    ActionKind.MOVE_TO,
    ActionKind.ROTATE,
    ActionKind.ROTATE_TO,
)

#: G1 action name per kind.
ACTION_NAME_BY_KIND: Dict[ActionKind, str] = {
    ActionKind.MOVE: _FACTORY_PREFIX + "MoveAction",
    ActionKind.MOVE_TO: _FACTORY_PREFIX + "MoveToAction",
    ActionKind.ROTATE: _FACTORY_PREFIX + "RotateAction",
    ActionKind.ROTATE_TO: _FACTORY_PREFIX + "RotateToAction",
}

#: Slamtec original names accepted as aliases. ``MoveByAction`` is Slamtec's
#: teleop primitive, which maps to our ``move``.
_SLAMTEC_PREFIX = "slamtec.agent.actions."
ACTION_ALIASES: Dict[str, ActionKind] = {
    _SLAMTEC_PREFIX + "MoveToAction": ActionKind.MOVE_TO,
    _SLAMTEC_PREFIX + "MoveByAction": ActionKind.MOVE,
    _SLAMTEC_PREFIX + "RotateAction": ActionKind.ROTATE,
    _SLAMTEC_PREFIX + "RotateToAction": ActionKind.ROTATE_TO,
}

#: Every accepted ``action_name`` -> kind.
KIND_BY_ACTION_NAME: Dict[str, ActionKind] = {
    name: kind for kind, name in ACTION_NAME_BY_KIND.items()
}
KIND_BY_ACTION_NAME.update(ACTION_ALIASES)

#: The ``SafetyConfig`` flag that must be enabled for each kind.
#: ``move`` is teleop (no obstacle avoidance) -> ``allow_motion``; the other
#: three are planned motion -> ``allow_navigation``.
REQUIRED_SAFETY_FLAG_BY_KIND: Dict[ActionKind, str] = {
    ActionKind.MOVE: "allow_motion",
    ActionKind.MOVE_TO: "allow_navigation",
    ActionKind.ROTATE: "allow_navigation",
    ActionKind.ROTATE_TO: "allow_navigation",
}


@dataclass(frozen=True)
class ActionParams:
    """Base class for every action's typed parameters."""

    def to_dict(self) -> Dict[str, Any]:
        return {}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionParams":
        return cls()


@dataclass(frozen=True)
class MoveParams(ActionParams):
    """Velocity teleop for a bounded duration. **Does NOT avoid obstacles.**

    :param vx_mps: forward velocity, m/s. Deadzone 0.2.
    :param vy_mps: lateral velocity, m/s. Deadzone 0.2.
    :param omega_radps: yaw rate, rad/s, CCW positive. Deadzone 0.3.
    :param duration_ms: how long to drive; bounded (default ceiling 3000 ms).
    """

    vx_mps: float = 0.0
    vy_mps: float = 0.0
    omega_radps: float = 0.0
    duration_ms: int = 500

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vx": self.vx_mps,
            "vy": self.vy_mps,
            "omega": self.omega_radps,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MoveParams":
        return cls(
            vx_mps=float(data.get("vx", data.get("vx_mps", 0.0))),
            vy_mps=float(data.get("vy", data.get("vy_mps", 0.0))),
            omega_radps=float(data.get("omega", data.get("omega_radps", 0.0))),
            duration_ms=int(data.get("duration_ms", 500) or 500),
        )


@dataclass(frozen=True)
class MoveToParams(ActionParams):
    """Navigate to a target pose. ``with_yaw`` is a **required explicit boolean**:
    ``False`` means the goal yaw is ignored (Slamtec semantics, but no silent
    flag trap)."""

    target: Point2D = field(default_factory=Point2D)
    yaw_rad: float = 0.0
    with_yaw: bool = False
    reach_threshold_m: float = 0.35
    yaw_threshold_rad: float = 0.5
    timeout_s: float = 120.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": {"x": self.target.x_m, "y": self.target.y_m},
            "yaw": self.yaw_rad,
            "with_yaw": self.with_yaw,
            "reach_threshold": self.reach_threshold_m,
            "yaw_threshold": self.yaw_threshold_rad,
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MoveToParams":
        target_data = data.get("target", {}) or {}
        return cls(
            target=Point2D(
                x_m=float(target_data.get("x", target_data.get("x_m", 0.0))),
                y_m=float(target_data.get("y", target_data.get("y_m", 0.0))),
            ),
            yaw_rad=float(data.get("yaw", data.get("yaw_rad", 0.0))),
            with_yaw=bool(data.get("with_yaw", False)),
            reach_threshold_m=float(data.get("reach_threshold", 0.35)),
            yaw_threshold_rad=float(data.get("yaw_threshold", 0.5)),
            timeout_s=float(data.get("timeout_s", 120.0)),
        )


@dataclass(frozen=True)
class RotateParams(ActionParams):
    """Rotate in place.

    :param angle_rad: for ``rotate`` this is a **relative** angle (CCW positive);
        for ``rotate_to`` it is an **absolute** heading. ``absolute`` records
        which -- the famous Slamtec trap, made explicit.
    """

    angle_rad: float = 0.0
    absolute: bool = False

    def to_dict(self) -> Dict[str, Any]:
        key = "yaw" if self.absolute else "angle"
        return {key: self.angle_rad, "absolute": self.absolute}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RotateParams":
        absolute = bool(data.get("absolute", False))
        key = "yaw" if absolute else "angle"
        value = data.get(key, data.get("angle_rad", data.get("angle", 0.0)))
        return cls(
            angle_rad=float(value),
            absolute=absolute,
        )


PARAMS_BY_KIND: Dict[ActionKind, Type[ActionParams]] = {
    ActionKind.MOVE: MoveParams,
    ActionKind.MOVE_TO: MoveToParams,
    ActionKind.ROTATE: RotateParams,
    ActionKind.ROTATE_TO: RotateParams,
}


def params_from_dict(kind: ActionKind, data: Optional[Dict[str, Any]]) -> ActionParams:
    resolved = ActionKind(kind)
    params_cls = PARAMS_BY_KIND.get(resolved)
    if params_cls is None:
        raise ValidationError(
            "unknown action kind %r" % (getattr(kind, "value", kind),),
            field="action_name",
            source="models.action",
        )
    return params_cls.from_dict(data or {})


@dataclass(frozen=True)
class ActionRequest:
    kind: ActionKind = ActionKind.UNKNOWN
    params: ActionParams = field(default_factory=ActionParams)
    requester: Optional[str] = None

    @property
    def required_safety_flag(self) -> str:
        return REQUIRED_SAFETY_FLAG_BY_KIND.get(ActionKind(self.kind), "allow_navigation")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "action_name": ACTION_NAME_BY_KIND.get(ActionKind(self.kind), str(self.kind)),
            "options": self.params.to_dict(),
        }
        if self.requester:
            out["requester"] = self.requester
        return out

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ActionRequest":
        name = str(data.get("action_name", ""))
        kind = KIND_BY_ACTION_NAME.get(name)
        if kind is None:
            raise ValidationError(
                "unknown action_name %r" % (name,),
                field="action_name",
                expected="one of %s" % ", ".join(sorted(KIND_BY_ACTION_NAME)),
                source="models.action",
            )
        return ActionRequest(
            kind=kind,
            params=params_from_dict(kind, data.get("options")),
            requester=data.get("requester"),
        )


@dataclass(frozen=True)
class ActionProgress:
    stage: ActionStage = ActionStage.UNKNOWN
    stage_text: Optional[str] = None
    percent: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"stage": self.stage_text or str(self.stage)}
        if self.percent is not None:
            out["percent"] = self.percent
        return out

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> "ActionProgress":
        data = data or {}
        return ActionProgress(
            stage=ActionStage.parse(data.get("stage")),
            stage_text=data.get("stage"),
            percent=None if data.get("percent") is None else float(data["percent"]),
        )


@dataclass(frozen=True)
class ActionRecord:
    id: str = ""
    compat_id: int = 0
    kind: ActionKind = ActionKind.UNKNOWN
    lifecycle: ActionLifecycle = ActionLifecycle.PENDING
    progress: ActionProgress = field(default_factory=ActionProgress)
    result: Optional[ActionResult] = None
    failure_code: Optional[FailureCode] = None
    reason: str = ""
    created_at: Stamp = field(default_factory=Stamp.zero)
    updated_at: Stamp = field(default_factory=Stamp.zero)
    params: Optional[ActionParams] = None
    requester: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return ActionLifecycle(self.lifecycle).is_terminal

    @property
    def is_active(self) -> bool:
        return ActionLifecycle(self.lifecycle).is_active

    @property
    def action_name(self) -> str:
        return ACTION_NAME_BY_KIND.get(ActionKind(self.kind), str(self.kind))

    @property
    def succeeded(self) -> bool:
        return self.lifecycle is ActionLifecycle.SUCCEEDED

    def transition(
        self,
        lifecycle: ActionLifecycle,
        stamp: Stamp,
        result: Optional[ActionResult] = None,
        failure_code: Optional[FailureCode] = None,
        reason: Optional[str] = None,
        progress: Optional[ActionProgress] = None,
    ) -> "ActionRecord":
        target = ActionLifecycle(lifecycle)
        resolved_result = result
        if resolved_result is None and target.is_terminal:
            resolved_result = {
                ActionLifecycle.SUCCEEDED: ActionResult.SUCCESS,
                ActionLifecycle.FAILED: ActionResult.FAILED,
                ActionLifecycle.CANCELLED: ActionResult.CANCELLED,
            }.get(target, ActionResult.UNKNOWN)
        return replace(
            self,
            lifecycle=target,
            updated_at=stamp,
            result=resolved_result if resolved_result is not None else self.result,
            failure_code=failure_code if failure_code is not None else self.failure_code,
            reason=reason if reason is not None else self.reason,
            progress=progress if progress is not None else self.progress,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "compat_id": self.compat_id,
            "action_name": self.action_name,
            "kind": str(self.kind),
            "state": str(self.lifecycle),
            "progress": self.progress.to_dict(),
            "result": None if self.result is None else str(self.result),
            "failure_code": None if self.failure_code is None else str(self.failure_code),
            "reason": self.reason,
            "created_at": self.created_at.to_dict(),
            "updated_at": self.updated_at.to_dict(),
            "params": self.params.to_dict() if self.params is not None else None,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ActionRecord":
        kind = ActionKind(data.get("kind", ActionKind.UNKNOWN))
        return ActionRecord(
            id=str(data.get("id", "")),
            compat_id=int(data.get("compat_id", 0) or 0),
            kind=kind,
            lifecycle=ActionLifecycle.parse(data.get("state"), ActionLifecycle.UNKNOWN),
            progress=ActionProgress.from_dict(data.get("progress")),
            result=None if data.get("result") is None else ActionResult(data["result"]),
            failure_code=(
                None if data.get("failure_code") is None else FailureCode(data["failure_code"])
            ),
            reason=str(data.get("reason", "")),
            created_at=Stamp.from_dict(data.get("created_at")),
            updated_at=Stamp.from_dict(data.get("updated_at")),
            params=(
                params_from_dict(kind, data["params"])
                if data.get("params") is not None
                else None
            ),
            requester=data.get("requester"),
        )
