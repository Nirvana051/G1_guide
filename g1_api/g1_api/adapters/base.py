"""The adapter contract. Every backend implements it; the core and services call it.

Everything above this file speaks canonical models; everything below is free to
speak whatever the hardware speaks. Partial backends degrade -- each
``NotImplemented*`` companion raises ``CapabilityAbsentError`` (501) from every
method.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # pragma: no cover
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

    def runtime_checkable(cls):  # type: ignore
        return cls

from g1_api.errors import CapabilityAbsentError, FailureCode, ValidationError
from g1_api.models.action import ActionProgress, ActionRecord
from g1_api.models.enums import ActionLifecycle, ActionResult
from g1_api.models.geometry import Pose2D
from g1_api.models.map import MapManifest, MapPackage, MappingStatus
from g1_api.models.state import (
    CapabilityInfo,
    DeviceIdentity,
    HealthState,
    LocalizationStatus,
    SafetyStatus,
)

__all__ = [
    "ProgressCallback",
    "ActionOutcome",
    "SystemAdapter",
    "LocalizationAdapter",
    "MapAdapter",
    "MotionAdapter",
    "VoiceAdapter",
    "ArmAdapter",
    "HandAdapter",
    "RobotAdapter",
    "NotImplementedSystemAdapter",
    "NotImplementedLocalizationAdapter",
    "NotImplementedMapAdapter",
    "NotImplementedMotionAdapter",
    "NotImplementedVoiceAdapter",
    "NotImplementedArmAdapter",
    "NotImplementedHandAdapter",
    "BaseRobotAdapter",
]

ProgressCallback = Callable[[ActionProgress], None]


@dataclass(frozen=True)
class ActionOutcome:
    lifecycle: ActionLifecycle = ActionLifecycle.SUCCEEDED
    failure_code: Optional[FailureCode] = None
    reason: str = ""
    progress: Optional[ActionProgress] = None

    def __post_init__(self) -> None:
        resolved = ActionLifecycle(self.lifecycle)
        if not resolved.is_terminal:
            raise ValidationError(
                "an ActionOutcome must carry a terminal lifecycle, got %s" % (resolved,),
                field="lifecycle",
                source="adapters.base",
            )
        object.__setattr__(self, "lifecycle", resolved)

    @staticmethod
    def success(reason: str = "", progress: Optional[ActionProgress] = None) -> "ActionOutcome":
        return ActionOutcome(ActionLifecycle.SUCCEEDED, reason=reason, progress=progress)

    @staticmethod
    def failed(
        failure_code: FailureCode = FailureCode.UNKNOWN,
        reason: str = "",
        progress: Optional[ActionProgress] = None,
    ) -> "ActionOutcome":
        return ActionOutcome(ActionLifecycle.FAILED, FailureCode(failure_code), reason, progress)

    @staticmethod
    def cancelled(reason: str = "cancelled by request") -> "ActionOutcome":
        return ActionOutcome(ActionLifecycle.CANCELLED, FailureCode.CANCELLED_BY_USER, reason)

    @property
    def result(self) -> ActionResult:
        return {
            ActionLifecycle.SUCCEEDED: ActionResult.SUCCESS,
            ActionLifecycle.FAILED: ActionResult.FAILED,
            ActionLifecycle.CANCELLED: ActionResult.CANCELLED,
        }.get(self.lifecycle, ActionResult.UNKNOWN)

    @property
    def succeeded(self) -> bool:
        return self.lifecycle is ActionLifecycle.SUCCEEDED


@runtime_checkable
class SystemAdapter(Protocol):
    async def get_identity(self) -> DeviceIdentity: ...
    async def get_capabilities(self) -> Tuple[CapabilityInfo, ...]: ...
    async def get_health(self) -> HealthState: ...
    async def get_safety_status(self) -> SafetyStatus: ...


@runtime_checkable
class LocalizationAdapter(Protocol):
    async def get_pose(self) -> Pose2D: ...
    async def get_status(self) -> LocalizationStatus: ...
    async def relocalize(self, x: float, y: float, yaw: float) -> LocalizationStatus: ...


@runtime_checkable
class MapAdapter(Protocol):
    async def list_maps(self) -> List[MapManifest]: ...
    async def upload_map(self, package: MapPackage) -> MapManifest: ...
    async def download_map(self, map_id: str) -> MapPackage: ...
    async def get_current_map(self) -> Optional[MapManifest]: ...
    async def select_map(self, map_id: str) -> MapManifest: ...
    async def delete_map(self, map_id: str) -> bool: ...
    async def set_tour_points(self, map_id: str, tour_points) -> MapManifest: ...
    async def start_mapping(self) -> "MappingStatus": ...
    async def mapping_status(self) -> "MappingStatus": ...
    async def finish_mapping(self, map_id: str, name: str = "") -> MapManifest: ...
    async def cancel_mapping(self) -> bool: ...


@runtime_checkable
class MotionAdapter(Protocol):
    async def execute_action(
        self,
        record: ActionRecord,
        report_progress: ProgressCallback,
        cancel_event: asyncio.Event,
    ) -> ActionOutcome: ...

    async def stop(self) -> None: ...


@runtime_checkable
class VoiceAdapter(Protocol):
    async def speak(self, text: str, speaker_id: int = 0) -> float: ...
    async def get_volume(self) -> int: ...
    async def set_volume(self, volume: int) -> int: ...
    async def set_led(self, r: int, g: int, b: int) -> None: ...


@runtime_checkable
class ArmAdapter(Protocol):
    async def get_action_list(self) -> Dict[int, str]: ...
    async def execute_action(self, action_id: int) -> None: ...


@runtime_checkable
class HandAdapter(Protocol):
    async def send_command(self, cmd: str) -> str: ...


@runtime_checkable
class RobotAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def system(self) -> SystemAdapter: ...

    @property
    def localization(self) -> LocalizationAdapter: ...

    @property
    def map(self) -> MapAdapter: ...

    @property
    def motion(self) -> MotionAdapter: ...

    @property
    def voice(self) -> VoiceAdapter: ...

    @property
    def arm(self) -> ArmAdapter: ...

    @property
    def hand(self) -> HandAdapter: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def ready(self) -> bool: ...


class _AbsentMixin(object):
    capability_group = "adapter"

    def _absent(self, operation: str) -> CapabilityAbsentError:
        return CapabilityAbsentError(
            capability="%s.%s" % (self.capability_group, operation),
            source="adapters.base",
        )


class NotImplementedSystemAdapter(_AbsentMixin):
    capability_group = "system"

    async def get_identity(self) -> DeviceIdentity:
        raise self._absent("get_identity")

    async def get_capabilities(self) -> Tuple[CapabilityInfo, ...]:
        raise self._absent("get_capabilities")

    async def get_health(self) -> HealthState:
        raise self._absent("get_health")

    async def get_safety_status(self) -> SafetyStatus:
        raise self._absent("get_safety_status")


class NotImplementedLocalizationAdapter(_AbsentMixin):
    capability_group = "localization"

    async def get_pose(self) -> Pose2D:
        raise self._absent("get_pose")

    async def get_status(self) -> LocalizationStatus:
        raise self._absent("get_status")

    async def relocalize(self, x: float, y: float, yaw: float) -> LocalizationStatus:
        raise self._absent("relocalize")


class NotImplementedMapAdapter(_AbsentMixin):
    capability_group = "map"

    async def list_maps(self) -> List[MapManifest]:
        raise self._absent("list_maps")

    async def upload_map(self, package: MapPackage) -> MapManifest:
        raise self._absent("upload_map")

    async def download_map(self, map_id: str) -> MapPackage:
        raise self._absent("download_map")

    async def get_current_map(self) -> Optional[MapManifest]:
        raise self._absent("get_current_map")

    async def select_map(self, map_id: str) -> MapManifest:
        raise self._absent("select_map")

    async def delete_map(self, map_id: str) -> bool:
        raise self._absent("delete_map")

    async def set_tour_points(self, map_id: str, tour_points) -> MapManifest:
        raise self._absent("set_tour_points")

    async def start_mapping(self):
        raise self._absent("start_mapping")

    async def mapping_status(self):
        raise self._absent("mapping_status")

    async def finish_mapping(self, map_id: str, name: str = ""):
        raise self._absent("finish_mapping")

    async def cancel_mapping(self) -> bool:
        raise self._absent("cancel_mapping")


class NotImplementedMotionAdapter(_AbsentMixin):
    capability_group = "motion"

    async def execute_action(
        self, record: ActionRecord, report_progress: ProgressCallback, cancel_event: asyncio.Event
    ) -> ActionOutcome:
        raise self._absent("execute_action")

    async def stop(self) -> None:
        raise self._absent("stop")


class NotImplementedVoiceAdapter(_AbsentMixin):
    capability_group = "voice"

    async def speak(self, text: str, speaker_id: int = 0) -> float:
        raise self._absent("speak")

    async def get_volume(self) -> int:
        raise self._absent("get_volume")

    async def set_volume(self, volume: int) -> int:
        raise self._absent("set_volume")

    async def set_led(self, r: int, g: int, b: int) -> None:
        raise self._absent("set_led")


class NotImplementedArmAdapter(_AbsentMixin):
    capability_group = "arm"

    async def get_action_list(self) -> Dict[int, str]:
        raise self._absent("get_action_list")

    async def execute_action(self, action_id: int) -> None:
        raise self._absent("execute_action")


class NotImplementedHandAdapter(_AbsentMixin):
    capability_group = "hand"

    async def send_command(self, cmd: str) -> str:
        raise self._absent("send_command")


class BaseRobotAdapter(object):
    adapter_name = "base"

    def __init__(
        self,
        system: Optional[Any] = None,
        localization: Optional[Any] = None,
        map_adapter: Optional[Any] = None,
        motion: Optional[Any] = None,
        voice: Optional[Any] = None,
        arm: Optional[Any] = None,
        hand: Optional[Any] = None,
    ) -> None:
        self._system = system if system is not None else NotImplementedSystemAdapter()
        self._localization = localization if localization is not None else NotImplementedLocalizationAdapter()
        self._map = map_adapter if map_adapter is not None else NotImplementedMapAdapter()
        self._motion = motion if motion is not None else NotImplementedMotionAdapter()
        self._voice = voice if voice is not None else NotImplementedVoiceAdapter()
        self._arm = arm if arm is not None else NotImplementedArmAdapter()
        self._hand = hand if hand is not None else NotImplementedHandAdapter()
        self._started = False

    @property
    def name(self) -> str:
        return self.adapter_name

    @property
    def system(self) -> SystemAdapter:
        return self._system

    @property
    def localization(self) -> LocalizationAdapter:
        return self._localization

    @property
    def map(self) -> MapAdapter:
        return self._map

    @property
    def motion(self) -> MotionAdapter:
        return self._motion

    @property
    def voice(self) -> VoiceAdapter:
        return self._voice

    @property
    def arm(self) -> ArmAdapter:
        return self._arm

    @property
    def hand(self) -> HandAdapter:
        return self._hand

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def ready(self) -> bool:
        return self._started
