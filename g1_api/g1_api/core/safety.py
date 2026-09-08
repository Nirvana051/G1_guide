"""**The mandatory safety gate. The most important file in this project.**

Every state-changing operation passes through :meth:`SafetyGate.check` or
:meth:`SafetyGate.guard`. There is no side channel: services do not call
adapters directly, and the task manager will not reach an adapter without a
decision. The gate re-evaluates live state on every call.

* Every ``ALLOW_*`` flag defaults to ``False``.
* Interlocks are **named** (``roscore_down``, ``map_not_selected``,
  ``localization_not_initialized``, ``tour_running``, ...) and reported all at
  once in a ``409`` denial, with the flag or remedy named.
* Speed is **clamped, never rejected** (a NaN/negative is rejected, because it is
  a broken request, not a too-fast one).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional, Tuple

from g1_api.config import SafetyConfig
from g1_api.errors import (
    NotReadyError,
    RobotError,
    SafetyInterlockError,
    UnsafeRequestError,
)
from g1_api.models.action import ActionKind, REQUIRED_SAFETY_FLAG_BY_KIND
from g1_api.models.enums import OpenEnum
from g1_api.models.state import Interlock
from g1_api.core.clock import Clock
from g1_api.utils.ids import new_request_id
from g1_api.utils.logging import get_logger

__all__ = [
    "CommandClass",
    "DenialKind",
    "SafetyContext",
    "SafetyDecision",
    "SafetyGate",
    "REQUIRED_FLAG_BY_COMMAND_CLASS",
    "INTERLOCK_FOR_FLAG",
    "command_class_for_action_kind",
]

_log = get_logger("core.safety")


class CommandClass(OpenEnum):
    READ_ONLY = "READ_ONLY"
    STATE_CHANGE = "STATE_CHANGE"
    MOTION = "MOTION"               # teleop: MoveAction
    NAVIGATION = "NAVIGATION"       # MoveTo / Rotate / RotateTo
    MAP_WRITE = "MAP_WRITE"
    ARM = "ARM"
    HAND = "HAND"
    VOICE = "VOICE"
    TOUR = "TOUR"
    CONFIGURATION_WRITE = "CONFIGURATION_WRITE"
    SAFETY_CRITICAL = "SAFETY_CRITICAL"
    UNKNOWN = "UNKNOWN"


class DenialKind(object):
    INTERLOCK = "INTERLOCK"
    UNSAFE = "UNSAFE"
    NOT_READY = "NOT_READY"


REQUIRED_FLAG_BY_COMMAND_CLASS: Mapping[CommandClass, Optional[str]] = {
    CommandClass.READ_ONLY: None,
    CommandClass.STATE_CHANGE: None,
    CommandClass.MOTION: "allow_motion",
    CommandClass.NAVIGATION: "allow_navigation",
    CommandClass.MAP_WRITE: "allow_map_write",
    CommandClass.ARM: "allow_arm",
    CommandClass.HAND: "allow_hand",
    CommandClass.VOICE: "allow_voice",
    CommandClass.TOUR: "allow_tour",
    CommandClass.CONFIGURATION_WRITE: None,
    CommandClass.SAFETY_CRITICAL: None,
    CommandClass.UNKNOWN: None,
}

INTERLOCK_FOR_FLAG: Mapping[str, str] = {
    "allow_motion": "motion_not_allowed",
    "allow_navigation": "navigation_not_allowed",
    "allow_map_write": "map_write_not_allowed",
    "allow_arm": "arm_not_allowed",
    "allow_hand": "hand_not_allowed",
    "allow_voice": "voice_not_allowed",
    "allow_tour": "tour_not_allowed",
}

MOVING_CLASSES = (CommandClass.MOTION, CommandClass.NAVIGATION)

CHANGING_CLASSES = (
    CommandClass.STATE_CHANGE,
    CommandClass.MOTION,
    CommandClass.NAVIGATION,
    CommandClass.MAP_WRITE,
    CommandClass.ARM,
    CommandClass.HAND,
    CommandClass.VOICE,
    CommandClass.TOUR,
    CommandClass.CONFIGURATION_WRITE,
    CommandClass.SAFETY_CRITICAL,
    CommandClass.UNKNOWN,
)


def command_class_for_action_kind(kind: ActionKind) -> CommandClass:
    flag = REQUIRED_SAFETY_FLAG_BY_KIND.get(ActionKind(kind), "allow_navigation")
    return CommandClass.MOTION if flag == "allow_motion" else CommandClass.NAVIGATION


@dataclass(frozen=True)
class SafetyContext:
    command_id: str = ""
    caller: Optional[str] = None
    description: str = ""
    adapter_ready: bool = True
    estop_engaged: bool = False
    hardware_fault: bool = False
    roscore_down: bool = False
    move_base_down: bool = False
    velocity_bridge_down: bool = False
    dds_unreachable: bool = False
    map_not_selected: bool = True
    localization_not_initialized: bool = True
    tour_running: bool = False
    mapping_running: bool = False
    external_motion_running: bool = False
    action_kind: Optional[ActionKind] = None
    requested_linear_mps: Optional[float] = None
    requested_angular_radps: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.command_id:
            object.__setattr__(self, "command_id", new_request_id())

    def with_(self, **overrides: Any) -> "SafetyContext":
        return replace(self, **overrides)

    def audit_fields(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "command_id": self.command_id,
            "caller": self.caller,
            "description": self.description,
            "adapter_ready": self.adapter_ready,
        }
        if self.action_kind is not None:
            out["action_kind"] = str(self.action_kind)
        return out


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str = ""
    interlocks: Tuple[str, ...] = ()
    command_id: str = ""
    command_class: CommandClass = CommandClass.UNKNOWN
    required_flag: Optional[str] = None
    denial_kind: Optional[str] = None
    clamped_linear_mps: Optional[float] = None
    clamped_angular_radps: Optional[float] = None
    clamped: bool = False
    caller: Optional[str] = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def denied(self) -> bool:
        return not self.allowed

    def to_error(self) -> RobotError:
        if self.allowed:
            raise ValueError("to_error() called on an allowed SafetyDecision")
        details = dict(self.details)
        if self.denial_kind == DenialKind.UNSAFE:
            return UnsafeRequestError(
                self.reason,
                reason=details.get("unsafe_reason"),
                source="core.safety",
                details={"command_id": self.command_id, "interlocks": list(self.interlocks)},
            )
        if self.denial_kind == DenialKind.NOT_READY:
            return NotReadyError(
                self.reason,
                capability=details.get("capability", "adapter"),
                retry_after_s=details.get("retry_after_s", 1.0),
                source="core.safety",
                details={"command_id": self.command_id},
            )
        return SafetyInterlockError(
            self.reason,
            interlocks=self.interlocks,
            required_flag=self.required_flag,
            source="core.safety",
            details={"command_id": self.command_id, "command_class": str(self.command_class)},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "interlocks": list(self.interlocks),
            "command_id": self.command_id,
            "command_class": str(self.command_class),
            "required_flag": self.required_flag,
            "denial_kind": self.denial_kind,
            "clamped": self.clamped,
            "clamped_linear_mps": self.clamped_linear_mps,
            "clamped_angular_radps": self.clamped_angular_radps,
            "caller": self.caller,
            "details": dict(self.details),
        }


class SafetyGate(object):
    def __init__(
        self,
        config: Optional[SafetyConfig] = None,
        clock: Optional[Clock] = None,
        audit_size: int = 256,
    ) -> None:
        self._config = config if config is not None else SafetyConfig()
        self._clock = clock if clock is not None else Clock(name="safety")
        self._lock = threading.RLock()
        self._audit: Any = deque(maxlen=int(audit_size))

    @property
    def config(self) -> SafetyConfig:
        return self._config

    def update_config(self, config: SafetyConfig) -> SafetyConfig:
        with self._lock:
            self._config = config
            return self._config

    def check(
        self, command_class: CommandClass, context: Optional[SafetyContext] = None
    ) -> SafetyDecision:
        klass = CommandClass(command_class)
        ctx = context if context is not None else SafetyContext()

        if klass is CommandClass.READ_ONLY:
            return self._audit_decision(
                SafetyDecision(
                    allowed=True, reason="read-only", command_id=ctx.command_id,
                    command_class=klass, caller=ctx.caller,
                ),
                ctx,
            )

        interlocks: List[str] = []
        denial_kind: Optional[str] = None
        details: Dict[str, Any] = {}

        required_flag = REQUIRED_FLAG_BY_COMMAND_CLASS.get(klass)
        if required_flag is not None and not getattr(self._config, required_flag, False):
            interlocks.append(INTERLOCK_FOR_FLAG[required_flag])
            denial_kind = DenialKind.INTERLOCK

        if klass in CHANGING_CLASSES:
            if not ctx.adapter_ready:
                interlocks.append(Interlock.ADAPTER_NOT_READY)
                denial_kind = DenialKind.NOT_READY
                details["capability"] = "adapter"
            if ctx.estop_engaged:
                interlocks.append(Interlock.ESTOP_ENGAGED)
            if ctx.hardware_fault:
                interlocks.append(Interlock.HARDWARE_FAULT)
            if ctx.roscore_down:
                interlocks.append(Interlock.ROSCORE_DOWN)
            if ctx.dds_unreachable:
                interlocks.append(Interlock.DDS_UNREACHABLE)

        if klass in MOVING_CLASSES:
            if ctx.velocity_bridge_down:
                interlocks.append(Interlock.VELOCITY_BRIDGE_DOWN)

        if klass is CommandClass.NAVIGATION:
            if ctx.move_base_down:
                interlocks.append(Interlock.MOVE_BASE_DOWN)
            if ctx.map_not_selected:
                interlocks.append(Interlock.MAP_NOT_SELECTED)
            if ctx.localization_not_initialized:
                interlocks.append(Interlock.LOCALIZATION_NOT_INITIALIZED)

        if klass in (CommandClass.MOTION, CommandClass.NAVIGATION, CommandClass.ARM, CommandClass.HAND):
            if ctx.tour_running:
                interlocks.append(Interlock.TOUR_RUNNING)

        # A mapping session owns the SLAM stack: navigation and tours are
        # refused, but MOTION (teleop) stays available -- it drives the run.
        if klass in (CommandClass.NAVIGATION, CommandClass.TOUR):
            if ctx.mapping_running:
                interlocks.append(Interlock.MAPPING_RUNNING)

        # An external replay owns the arms (rt/arm_sdk weight = 1): official
        # arm actions would fight it, and walking mid-replay is refused
        # conservatively. MOTION (teleop) stays available as an escape hatch.
        if klass in (CommandClass.ARM, CommandClass.NAVIGATION, CommandClass.TOUR):
            if ctx.external_motion_running:
                interlocks.append(Interlock.EXTERNAL_MOTION_RUNNING)

        # Speed handling.
        clamp_error = self._validate_speeds(ctx)
        if clamp_error is not None:
            decision = SafetyDecision(
                allowed=False,
                reason=clamp_error,
                interlocks=tuple(dict.fromkeys(interlocks)),
                command_id=ctx.command_id,
                command_class=klass,
                required_flag=required_flag,
                denial_kind=DenialKind.UNSAFE,
                caller=ctx.caller,
                details={"unsafe_reason": "invalid_speed"},
            )
            return self._audit_decision(decision, ctx)

        linear, angular, clamped = self._clamp_speeds(ctx)
        deduped = tuple(dict.fromkeys(interlocks))
        if deduped:
            decision = SafetyDecision(
                allowed=False,
                reason="refused by safety gate: %s" % ", ".join(deduped),
                interlocks=deduped,
                command_id=ctx.command_id,
                command_class=klass,
                required_flag=(
                    required_flag
                    if required_flag is not None
                    and INTERLOCK_FOR_FLAG.get(required_flag) in deduped
                    else None
                ),
                denial_kind=denial_kind or DenialKind.INTERLOCK,
                clamped_linear_mps=linear,
                clamped_angular_radps=angular,
                clamped=clamped,
                caller=ctx.caller,
                details=details,
            )
            return self._audit_decision(decision, ctx)

        decision = SafetyDecision(
            allowed=True,
            reason="permitted" if not clamped else "permitted with speed clamped",
            command_id=ctx.command_id,
            command_class=klass,
            required_flag=required_flag,
            clamped_linear_mps=linear,
            clamped_angular_radps=angular,
            clamped=clamped,
            caller=ctx.caller,
        )
        return self._audit_decision(decision, ctx)

    @asynccontextmanager
    async def guard(
        self, command_class: CommandClass, context: Optional[SafetyContext] = None
    ) -> AsyncIterator[SafetyDecision]:
        decision = self.check(command_class, context)
        if not decision.allowed:
            raise decision.to_error()
        yield decision

    def active_interlocks(
        self, context: SafetyContext, command_class: CommandClass = CommandClass.NAVIGATION
    ) -> Tuple[str, ...]:
        decision = self.check(command_class, context)
        return decision.interlocks

    def _validate_speeds(self, ctx: SafetyContext) -> Optional[str]:
        for name, value in (
            ("requested_linear_mps", ctx.requested_linear_mps),
            ("requested_angular_radps", ctx.requested_angular_radps),
        ):
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                return "%s is not a number" % (name,)
            if math.isnan(number) or math.isinf(number):
                return "%s must be finite, got %r" % (name, value)
            if name == "requested_linear_mps" and number < 0:
                return "requested_linear_mps must be >= 0"
        return None

    def _clamp_speeds(self, ctx: SafetyContext) -> Tuple[Optional[float], Optional[float], bool]:
        clamped = False
        linear = ctx.requested_linear_mps
        angular = ctx.requested_angular_radps
        if linear is not None and float(linear) > float(self._config.max_linear_speed_mps):
            linear = float(self._config.max_linear_speed_mps)
            clamped = True
        if angular is not None and abs(float(angular)) > float(self._config.max_angular_speed_radps):
            angular = math.copysign(float(self._config.max_angular_speed_radps), float(angular))
            clamped = True
        return (linear, angular, clamped)

    def recent_decisions(self, limit: Optional[int] = None) -> Tuple[SafetyDecision, ...]:
        with self._lock:
            items = list(self._audit)
        if limit is not None:
            items = items[-int(limit):]
        return tuple(items)

    def _audit_decision(self, decision: SafetyDecision, ctx: SafetyContext) -> SafetyDecision:
        with self._lock:
            self._audit.append(decision)
        return decision
