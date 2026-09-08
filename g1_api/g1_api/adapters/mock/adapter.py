"""The mock backend: full simulation, the default.

* Navigation ``move_to`` interpolates the simulated pose toward the target at a
  nominal speed, then rotates to the goal yaw when ``with_yaw`` is set.
* ``move`` holds the commanded velocity for ``duration_ms``.
* TTS logs the text and returns ``len(text) * char_duration_s``.
* Maps are stored as directories under ``map_storage_dir/<map_id>/``; selecting
  a map sets ``localization_not_initialized`` (the real launch flow requires a
  relocalization after a map switch).
* Fault injection covers ``GOAL_UNREACHABLE`` and TTS failure.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

from g1_api.adapters.base import BaseRobotAdapter, ActionOutcome
from g1_api.config import AppConfig
from g1_api.errors import ConflictError, FailureCode, NotFoundError, ValidationError
from g1_api.models.action import ActionRecord, MoveParams, MoveToParams, RotateParams
from g1_api.models.enums import ActionKind, ActionStage
from g1_api.models.geometry import Pose2D, angle_difference_rad, normalize_angle_rad
from g1_api.models.map import MapManifest, MapPackage, MappingStatus
from g1_api.models.state import (
    CapabilityInfo,
    DeviceIdentity,
    HealthState,
    LocalizationStatus,
    SafetyStatus,
    collect_interlocks,
)

__all__ = ["MockAdapter", "MockWorld", "DEFAULT_ARM_ACTIONS"]

#: The arm action table (appendix A.7), returned by the mock ``action-list``.
DEFAULT_ARM_ACTIONS: Dict[int, str] = {
    99: "release arm(复位)",
    11: "two-hand kiss",
    12: "left kiss",
    13: "right kiss",
    15: "hands up",
    17: "clap",
    18: "high five",
    19: "hug",
    20: "heart",
    21: "right heart",
    22: "reject",
    23: "right hand up",
    24: "x-ray",
    25: "face wave",
    26: "high wave",
    27: "shake hand",
}

_HAND_COMMANDS = {
    "1": "Left hand open", "2": "Left hand fist", "3": "Left hand half grip",
    "4": "Left hand pinch", "5": "Left hand bottle grip", "6": "Left hand peace sign",
    "7": "Right hand open", "8": "Right hand fist", "9": "Right hand half grip",
    "10": "Right hand pinch", "11": "Right hand bottle grip", "12": "Right hand peace sign",
    "13": "Both hands open", "14": "Both hands fist", "15": "Both hands half grip",
    "16": "Both hands pinch", "17": "Both hands bottle grip", "18": "Both hands soft grip",
    "19": "Both hands alternating", "s": "Query status",
}


class MockWorld(object):
    """Mutable simulated ground truth, shared by the mock sub-adapters.

    Tests reach it through ``core.adapter.world`` to inject faults or inspect
    pose (mirroring the reference project's ``mock_world`` fixture).
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self.pose = Pose2D(0.0, 0.0, 0.0, "map")
        self.velocity = (0.0, 0.0, 0.0)
        self.current_map_id: Optional[str] = None
        self.localization_initialized = False
        self.estop_engaged = False
        self.volume = 100
        self.led = (0, 255, 0)
        self.tts_log: List[str] = []
        self.arm_log: List[int] = []
        self.hand_log: List[str] = []
        self.maps: Dict[str, Dict[str, Any]] = {}  # map_id -> {"manifest":..., "files":...}
        self.mapping_active = False
        self.mapping_started_utc: Optional[float] = None
        self.fault_goal_unreachable = False
        self.fault_goal_unreachable_uses = 0
        self.fault_tts_fail = False
        self.arm_actions = dict(DEFAULT_ARM_ACTIONS)

    def set_pose(self, x: float, y: float, yaw: float) -> None:
        self.pose = Pose2D(x, y, yaw, "map")

    def set_estop(self, engaged: bool) -> None:
        self.estop_engaged = engaged

    def set_fault_goal_unreachable(self, on: bool) -> None:
        self.fault_goal_unreachable = on

    def fail_next_goal(self, n: int = 1) -> None:
        self.fault_goal_unreachable_uses = max(0, int(n))

    def set_fault_tts_fail(self, on: bool) -> None:
        self.fault_tts_fail = on


class MockSystemAdapter(object):
    def __init__(self, config: AppConfig, world: MockWorld) -> None:
        self._config = config
        self._world = world

    async def get_identity(self) -> DeviceIdentity:
        return DeviceIdentity(
            manufacturer_name=self._config.identity.manufacturer_name,
            model_name=self._config.identity.model_name,
            software_version=self._config.identity.software_version,
            device_id=self._config.identity.device_id or "mock-device-001",
            mode=self._config.mode.mode,
        )

    async def get_capabilities(self) -> Tuple[CapabilityInfo, ...]:
        return (
            CapabilityInfo("navigation", True, "MoveTo/Rotate/RotateTo over the simulated map."),
            CapabilityInfo("tts", True, "Text-to-speech; mock logs text and returns an estimate."),
            CapabilityInfo("arm", True, "G1 arm action client (mock)."),
            CapabilityInfo("hand", True, "Dexterous hand TCP command (mock)."),
            CapabilityInfo("tour", True, "Guided tour orchestration above the core API."),
        )

    async def get_safety_status(self) -> SafetyStatus:
        w = self._world
        interlocks = collect_interlocks(
            estop=w.estop_engaged,
            map_not_selected=w.current_map_id is None,
            localization_not_initialized=(w.current_map_id is not None and not w.localization_initialized)
            if w.current_map_id is not None else False,
            mapping_running=w.mapping_active,
            adapter_ready=True,
        )
        return SafetyStatus(
            estop_engaged=w.estop_engaged,
            map_not_selected=w.current_map_id is None,
            localization_not_initialized=(
                w.current_map_id is not None and not w.localization_initialized
            ),
            mapping_running=w.mapping_active,
            active_interlocks=interlocks,
        )

    async def get_health(self) -> HealthState:
        safety = await self.get_safety_status()
        base_error = []
        if safety.estop_engaged:
            base_error.append({"component": "MOTION", "level": 2, "message": "emergency stop engaged"})
        return HealthState(
            has_warning=False,
            has_error=safety.estop_engaged,
            has_fatal=False,
            base_error=tuple(base_error),
            interlocks=safety.active_interlocks,
        )


class MockLocalizationAdapter(object):
    def __init__(self, world: MockWorld) -> None:
        self._world = world

    async def get_pose(self) -> Pose2D:
        return self._world.pose

    async def get_status(self) -> LocalizationStatus:
        w = self._world
        note = (
            "FAST-LIO has no 0-100 quality score; only initialized/not-initialized is reported."
            if w.localization_initialized
            else "localization not initialized; call /localization/:relocalize"
        )
        return LocalizationStatus(
            initialized=w.localization_initialized,
            map_id=w.current_map_id,
            note=note,
            pose=w.pose if w.localization_initialized else None,
        )

    async def relocalize(self, x: float, y: float, yaw: float) -> LocalizationStatus:
        if self._world.mapping_active:
            raise ConflictError(
                "a mapping session is running; finish or cancel it before relocalizing",
                source="adapters.mock",
            )
        self._world.set_pose(x, y, yaw)
        self._world.localization_initialized = True
        return await self.get_status()


class MockMapAdapter(object):
    def __init__(self, config: AppConfig, world: MockWorld) -> None:
        self._config = config
        self._world = world
        self._root = os.path.expanduser(config.map.map_storage_dir)

    def _dir(self, map_id: str) -> str:
        return os.path.join(self._root, map_id)

    async def list_maps(self) -> List[MapManifest]:
        return [entry["manifest"] for entry in self._world.maps.values()]

    async def upload_map(self, package: MapPackage) -> MapManifest:
        manifest = package.manifest
        self._world.maps[manifest.map_id] = {"manifest": manifest, "files": dict(package.files)}
        # Persist files to disk for realism (best effort; never fatal).
        try:
            d = self._dir(manifest.map_id)
            os.makedirs(d, exist_ok=True)
            for name, data in package.files.items():
                with open(os.path.join(d, name), "wb") as handle:
                    handle.write(data)
        except OSError:
            pass
        return manifest

    async def download_map(self, map_id: str) -> MapPackage:
        entry = self._world.maps.get(map_id)
        if entry is None:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.mock")
        return MapPackage(manifest=entry["manifest"], files=dict(entry["files"]))

    async def get_current_map(self) -> Optional[MapManifest]:
        if self._world.current_map_id is None:
            return None
        entry = self._world.maps.get(self._world.current_map_id)
        return None if entry is None else entry["manifest"]

    async def select_map(self, map_id: str) -> MapManifest:
        if self._world.mapping_active:
            raise ConflictError(
                "a mapping session is running; finish or cancel it before selecting a map",
                source="adapters.mock",
            )
        entry = self._world.maps.get(map_id)
        if entry is None:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.mock")
        self._world.current_map_id = map_id
        self._world.localization_initialized = False  # map switch requires relocalization
        return entry["manifest"]

    async def delete_map(self, map_id: str) -> bool:
        if map_id == self._world.current_map_id:
            raise ConflictError(
                "cannot delete the currently selected map; select another map first",
                source="adapters.mock",
            )
        if map_id not in self._world.maps:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.mock")
        del self._world.maps[map_id]
        return True

    async def start_mapping(self) -> MappingStatus:
        import time as _time

        w = self._world
        if w.mapping_active:
            raise ConflictError("a mapping session is already running", source="adapters.mock")
        w.mapping_active = True
        w.mapping_started_utc = _time.time()
        # A mapping run owns the SLAM stack: current localization is dropped.
        w.localization_initialized = False
        return await self.mapping_status()

    async def mapping_status(self) -> MappingStatus:
        import time as _time

        w = self._world
        elapsed = (_time.time() - w.mapping_started_utc) if (w.mapping_active and w.mapping_started_utc) else 0.0
        return MappingStatus(
            active=w.mapping_active,
            started_utc=w.mapping_started_utc if w.mapping_active else None,
            elapsed_s=elapsed,
            note="mock mapping session" if w.mapping_active else "idle",
        )

    async def finish_mapping(self, map_id: str, name: str = "") -> MapManifest:
        import time as _time

        w = self._world
        if not w.mapping_active:
            raise ConflictError("no mapping session is running", source="adapters.mock")
        w.mapping_active = False
        w.mapping_started_utc = None
        manifest = MapManifest(
            map_id=map_id,
            name=name or map_id,
            created_utc=_time.time(),
            resolution=0.05,
            origin=(0.0, 0.0, 0.0),
        )
        files = {
            "map.pcd": b"mock pcd",
            "ground_map.pcd": b"mock ground",
            "grid.pgm": b"P5\n1 1\n255\n\x00",
            "grid.yaml": b"image: grid.pgm\nresolution: 0.05\norigin: [0.0, 0.0, 0.0]\n",
        }
        return await self.upload_map(MapPackage(manifest=manifest, files=files))

    async def cancel_mapping(self) -> bool:
        w = self._world
        if not w.mapping_active:
            raise ConflictError("no mapping session is running", source="adapters.mock")
        w.mapping_active = False
        w.mapping_started_utc = None
        return True

    async def set_tour_points(self, map_id: str, tour_points) -> MapManifest:
        entry = self._world.maps.get(map_id)
        if entry is None:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.mock")
        manifest = entry["manifest"]
        updated = MapManifest(
            map_id=manifest.map_id,
            name=manifest.name,
            created_utc=manifest.created_utc,
            resolution=manifest.resolution,
            origin=manifest.origin,
            tour_points=tuple(tour_points),
            checksums=manifest.checksums,
        )
        entry["manifest"] = updated
        return updated


class MockMotionAdapter(object):
    _TICK_S = 0.02
    _SPEED_MPS = 1.0
    _ROTATE_SPEED = 1.5

    def __init__(self, world: MockWorld) -> None:
        self._world = world

    async def execute_action(self, record: ActionRecord, report_progress, cancel_event) -> ActionOutcome:
        kind = ActionKind(record.kind)
        params = record.params
        if kind is ActionKind.MOVE:
            return await self._move(params, report_progress, cancel_event)
        if kind is ActionKind.MOVE_TO:
            return await self._move_to(params, report_progress, cancel_event)
        if kind is ActionKind.ROTATE:
            return await self._rotate(params, cancel_event)
        if kind is ActionKind.ROTATE_TO:
            return await self._rotate_to(params, cancel_event)
        return ActionOutcome.failed(FailureCode.UNKNOWN, "unknown action kind")

    async def _move(self, params, report_progress, cancel_event) -> ActionOutcome:
        p = params  # type: MoveParams
        self._world.velocity = (p.vx_mps, p.vy_mps, p.omega_radps)
        report_progress(_progress(ActionStage.MOVING, 0.0))
        elapsed = 0.0
        duration = p.duration_ms / 1000.0
        while elapsed < duration:
            if cancel_event.is_set():
                self._world.velocity = (0.0, 0.0, 0.0)
                return ActionOutcome.cancelled("move cancelled")
            await asyncio.sleep(self._TICK_S)
            elapsed += self._TICK_S
            report_progress(_progress(ActionStage.MOVING, min(1.0, elapsed / duration)))
        self._world.velocity = (0.0, 0.0, 0.0)
        return ActionOutcome.success("move completed")

    async def _move_to(self, params, report_progress, cancel_event) -> ActionOutcome:
        if self._world.fault_goal_unreachable_uses > 0:
            self._world.fault_goal_unreachable_uses -= 1
            return ActionOutcome.failed(FailureCode.GOAL_UNREACHABLE, "goal unreachable (injected)")
        if self._world.fault_goal_unreachable:
            return ActionOutcome.failed(FailureCode.GOAL_UNREACHABLE, "goal unreachable (injected)")
        p = params  # type: MoveToParams
        target_x, target_y = p.target.x_m, p.target.y_m
        report_progress(_progress(ActionStage.GOING_TO_TARGET, 0.0))
        while True:
            if cancel_event.is_set():
                return ActionOutcome.cancelled("move_to cancelled")
            dx = target_x - self._world.pose.x_m
            dy = target_y - self._world.pose.y_m
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= p.reach_threshold_m:
                break
            step = min(self._SPEED_MPS * self._TICK_S, dist)
            heading = (dx / dist, dy / dist)
            self._world.set_pose(
                self._world.pose.x_m + heading[0] * step,
                self._world.pose.y_m + heading[1] * step,
                self._world.pose.yaw_rad,
            )
            await asyncio.sleep(self._TICK_S)
        if p.with_yaw:
            await self._rotate_to_yaw(p.yaw_rad, cancel_event, getattr(p, "yaw_threshold_rad", 0.15))
        report_progress(_progress(ActionStage.DONE, 1.0))
        return ActionOutcome.success("reached target")

    async def _rotate(self, params, cancel_event) -> ActionOutcome:
        p = params  # type: RotateParams
        target = normalize_angle_rad(self._world.pose.yaw_rad + p.angle_rad)
        await self._rotate_to_yaw(target, cancel_event)
        return ActionOutcome.success("rotation completed")

    async def _rotate_to(self, params, cancel_event) -> ActionOutcome:
        p = params  # type: RotateParams
        await self._rotate_to_yaw(p.angle_rad, cancel_event)
        return ActionOutcome.success("rotation completed")

    async def _rotate_to_yaw(self, target_yaw: float, cancel_event, threshold: float = 0.15) -> None:
        while True:
            if cancel_event.is_set():
                return
            diff = angle_difference_rad(target_yaw, self._world.pose.yaw_rad)
            if abs(diff) <= threshold:
                self._world.set_pose(self._world.pose.x_m, self._world.pose.y_m, target_yaw)
                return
            step = min(self._ROTATE_SPEED * self._TICK_S, abs(diff))
            sign = 1.0 if diff > 0 else -1.0
            self._world.set_pose(
                self._world.pose.x_m,
                self._world.pose.y_m,
                normalize_angle_rad(self._world.pose.yaw_rad + sign * step),
            )
            await asyncio.sleep(self._TICK_S)

    async def stop(self) -> None:
        self._world.velocity = (0.0, 0.0, 0.0)


def _progress(stage: ActionStage, percent: float) -> Any:
    from g1_api.models.action import ActionProgress

    return ActionProgress(stage=stage, stage_text=str(stage), percent=percent)


class MockVoiceAdapter(object):
    def __init__(self, config: AppConfig, world: MockWorld) -> None:
        self._config = config
        self._world = world

    def _estimate(self, text: str, speaker_id: int = 0) -> float:
        return len(text) * self._config.voice.char_duration_for(speaker_id)

    async def speak(self, text: str, speaker_id: int = 0) -> float:
        if self._world.fault_tts_fail:
            raise ValidationError("TTS failed (injected)", field="text", source="adapters.mock")
        self._world.tts_log.append(text)
        return self._estimate(text, speaker_id)

    async def get_volume(self) -> int:
        return self._world.volume

    async def set_volume(self, volume: int) -> int:
        self._world.volume = max(0, min(100, int(volume)))
        return self._world.volume

    async def set_led(self, r: int, g: int, b: int) -> None:
        self._world.led = (int(r), int(g), int(b))


class MockArmAdapter(object):
    def __init__(self, world: MockWorld) -> None:
        self._world = world

    async def get_action_list(self) -> Dict[int, str]:
        return dict(self._world.arm_actions)

    async def execute_action(self, action_id: int) -> None:
        self._world.arm_log.append(int(action_id))


class MockHandAdapter(object):
    def __init__(self, world: MockWorld) -> None:
        self._world = world

    async def send_command(self, cmd: str) -> str:
        cmd = str(cmd).strip()
        if cmd not in _HAND_COMMANDS:
            raise ValidationError(
                "unknown hand command %r" % (cmd,),
                field="cmd",
                expected="1..19 or s",
                source="adapters.mock",
            )
        self._world.hand_log.append(cmd)
        return "ok:%s" % (_HAND_COMMANDS[cmd],)


class MockAdapter(BaseRobotAdapter):
    adapter_name = "mock"

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self.world = MockWorld(config)
        super(MockAdapter, self).__init__(
            system=MockSystemAdapter(config, self.world),
            localization=MockLocalizationAdapter(self.world),
            map_adapter=MockMapAdapter(config, self.world),
            motion=MockMotionAdapter(self.world),
            voice=MockVoiceAdapter(config, self.world),
            arm=MockArmAdapter(self.world),
            hand=MockHandAdapter(self.world),
        )
