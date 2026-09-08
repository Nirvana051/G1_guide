"""The real G1 backend.

Lazy by construction: nothing imports ROS or DDS at module load. ``start()``
picks the DDS layer from ``mode.dds_backend`` (``real`` = unitree_sdk2py with
the LocoClient-before-AudioClient order; ``stub`` = log-only stand-ins for
simulation) and prepares the ROS bridge and the navigation launch manager.

The ROS side (navigation / localization / map switching) is identical in both
DDS backends, which is what makes the Gazebo simulation a faithful test of the
real code path: only Loco/Audio/Arm calls are stubbed there, the rest is the
same rospy + roslaunch machinery that runs on the robot's PC2.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from g1_api.adapters.base import BaseRobotAdapter, ActionOutcome
from g1_api.config import AppConfig
from g1_api.errors import AdapterError, ConflictError, FailureCode, NotFoundError, ValidationError
from g1_api.models.action import ActionProgress, ActionRecord, MoveParams, MoveToParams, RotateParams
from g1_api.models.enums import ActionKind, ActionStage
from g1_api.models.geometry import Pose2D, normalize_angle_rad
from g1_api.models.map import MapManifest, MapPackage, MappingStatus, package_from_artifacts
from g1_api.models.state import (
    CapabilityInfo,
    DeviceIdentity,
    HealthState,
    LocalizationStatus,
    SafetyStatus,
    collect_interlocks,
)

__all__ = ["G1Adapter"]


def _progress(stage: ActionStage, percent: float) -> ActionProgress:
    return ActionProgress(stage=stage, stage_text=str(stage), percent=percent)


async def _in_thread(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn, *args)


class G1State(object):
    """Mutable adapter-side state shared by the sub-adapters."""

    def __init__(self) -> None:
        self.current_map_id: Optional[str] = None
        self.localization_initialized = False
        self.maps: Dict[str, MapManifest] = {}
        self.mapping_active = False
        self.mapping_started_utc: Optional[float] = None
        self.lock = threading.Lock()


class G1SystemAdapter(object):
    def __init__(self, config: AppConfig, adapter: "G1Adapter") -> None:
        self._config = config
        self._adapter = adapter

    async def get_identity(self) -> DeviceIdentity:
        return DeviceIdentity(
            manufacturer_name=self._config.identity.manufacturer_name,
            model_name=self._config.identity.model_name,
            software_version=self._config.identity.software_version,
            device_id=self._config.identity.device_id or "g1-unknown",
            mode="real",
        )

    async def get_capabilities(self) -> Tuple[CapabilityInfo, ...]:
        ready = self._adapter.ready()
        dds_note = "stubbed (simulation)" if self._adapter.dds_is_stub else "over DDS (real)"
        return (
            CapabilityInfo("navigation", ready, "Nav over move_base; three-phase TF arrival."),
            CapabilityInfo("tts", ready, "TTS %s." % dds_note),
            CapabilityInfo("arm", ready, "Arm actions %s." % dds_note),
            CapabilityInfo("hand", True, "Dexterous hand TCP %s:%s." % (
                self._config.hand.robot_ip or "?", self._config.hand.port)),
            CapabilityInfo("tour", ready, "Guided tour above the core API."),
        )

    async def get_safety_status(self) -> SafetyStatus:
        adapter = self._adapter
        state = adapter.state
        ros: Dict[str, bool] = {"roscore_down": True, "move_base_down": True,
                                "velocity_bridge_down": True}
        if adapter.ros_available():
            ros = await _in_thread(adapter.ros.ros_health)
        launch_crashed = adapter.launch_crashed()
        map_not_selected = state.current_map_id is None
        loc_uninit = not state.localization_initialized
        interlocks = collect_interlocks(
            roscore_down=ros.get("roscore_down", False),
            move_base_down=bool(ros.get("move_base_down", False) or launch_crashed),
            velocity_bridge_down=ros.get("velocity_bridge_down", False),
            dds_unreachable=not adapter.dds_initialized,
            map_not_selected=map_not_selected,
            localization_not_initialized=loc_uninit,
            mapping_running=state.mapping_active,
            adapter_ready=adapter.ready(),
        )
        return SafetyStatus(
            roscore_down=ros.get("roscore_down", False),
            move_base_down=bool(ros.get("move_base_down", False) or launch_crashed),
            velocity_bridge_down=ros.get("velocity_bridge_down", False),
            dds_unreachable=not adapter.dds_initialized,
            map_not_selected=map_not_selected,
            localization_not_initialized=loc_uninit,
            mapping_running=state.mapping_active,
            active_interlocks=interlocks,
        )

    async def get_health(self) -> HealthState:
        safety = await self.get_safety_status()
        base_error = []
        if safety.roscore_down:
            base_error.append({"component": "ROS", "level": 3, "message": "roscore unreachable"})
        if safety.move_base_down:
            base_error.append({"component": "NAV", "level": 2,
                               "message": "move_base not up (select a map to start navigation)"})
        if safety.velocity_bridge_down:
            base_error.append({"component": "MOTION", "level": 2,
                               "message": "/cmd_vel has no subscriber (velocity bridge down)"})
        return HealthState(
            has_warning=bool(base_error),
            has_error=safety.move_base_down or safety.velocity_bridge_down,
            has_fatal=safety.roscore_down,
            base_error=tuple(base_error),
            interlocks=safety.active_interlocks,
        )


class G1LocalizationAdapter(object):
    def __init__(self, adapter: "G1Adapter") -> None:
        self._adapter = adapter

    async def get_pose(self) -> Pose2D:
        self._adapter.require_ros("localization.get_pose")
        pose = await _in_thread(self._adapter.ros.get_pose_tuple)
        if pose is None:
            raise AdapterError(
                "TF map->body unavailable (is navigation running and localized?)",
                adapter="g1", operation="localization.get_pose", source="adapters.g1",
            )
        return Pose2D(pose[0], pose[1], pose[3], "map")

    async def get_status(self) -> LocalizationStatus:
        state = self._adapter.state
        note = (
            "FAST-LIO has no 0-100 quality score; only initialized/not-initialized is reported."
            if state.localization_initialized
            else "localization not initialized; call /localization/:relocalize"
        )
        pose = None
        if state.localization_initialized and self._adapter.ros_available():
            tup = await _in_thread(self._adapter.ros.get_pose_tuple)
            if tup is not None:
                pose = Pose2D(tup[0], tup[1], tup[3], "map")
        return LocalizationStatus(
            initialized=state.localization_initialized,
            map_id=state.current_map_id,
            note=note,
            pose=pose,
        )

    async def relocalize(self, x: float, y: float, yaw: float) -> LocalizationStatus:
        adapter = self._adapter
        adapter.require_ros("localization.relocalize")
        state = adapter.state
        if state.mapping_active:
            raise ConflictError(
                "a mapping session is running; finish or cancel it before relocalizing",
                source="adapters.g1",
            )
        if state.current_map_id is None:
            raise ConflictError(
                "no map selected; select a map before relocalizing", source="adapters.g1"
            )
        pcd_path = os.path.join(adapter.map_dir(state.current_map_id), "map.pcd")
        if not os.path.isfile(pcd_path):
            raise AdapterError(
                "selected map has no map.pcd at %s" % pcd_path,
                adapter="g1", operation="localization.relocalize", source="adapters.g1",
            )
        ok, message = await _in_thread(
            adapter.ros.call_slam_reloc, pcd_path, float(x), float(y), float(yaw)
        )
        if not ok:
            raise AdapterError(
                "relocalization request failed: %s" % message,
                adapter="g1", operation="localization.relocalize", source="adapters.g1",
            )
        confirmed = await _in_thread(adapter.ros.wait_reloc_confirmed, 20.0)
        if not confirmed:
            raise AdapterError(
                "relocalization not confirmed by /slam_reloc_check within 20s",
                adapter="g1", operation="localization.relocalize", source="adapters.g1",
            )
        state.localization_initialized = True
        return await self.get_status()


class G1MapAdapter(object):
    """Map library on disk + the map-switch launch choreography.

    ``:select`` is the hard part (course launch hardcodes the map paths): stop
    the managed roslaunch, restart it with the selected map's absolute paths,
    wait for /move_base + /slam_reloc, then require a fresh relocalization.
    """

    _REQUIRED_FILES = ("map.pcd", "ground_map.pcd", "grid.pgm", "grid.yaml")

    def __init__(self, config: AppConfig, adapter: "G1Adapter") -> None:
        self._config = config
        self._adapter = adapter

    async def list_maps(self) -> List[MapManifest]:
        return list(self._adapter.state.maps.values())

    async def upload_map(self, package: MapPackage) -> MapManifest:
        manifest = package.manifest
        d = self._adapter.map_dir(manifest.map_id)
        os.makedirs(d, exist_ok=True)
        for name, data in package.files.items():
            with open(os.path.join(d, name), "wb") as handle:
                handle.write(data)
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest.to_dict(), handle, ensure_ascii=False, indent=2)
        self._adapter.state.maps[manifest.map_id] = manifest
        return manifest

    async def download_map(self, map_id: str) -> MapPackage:
        manifest = self._adapter.state.maps.get(map_id)
        if manifest is None:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.g1")
        d = self._adapter.map_dir(map_id)
        files: Dict[str, bytes] = {}
        for name in self._REQUIRED_FILES:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    files[name] = handle.read()
        return MapPackage(manifest=manifest, files=files)

    async def get_current_map(self) -> Optional[MapManifest]:
        state = self._adapter.state
        if state.current_map_id is None:
            return None
        return state.maps.get(state.current_map_id)

    async def start_mapping(self) -> MappingStatus:
        adapter = self._adapter
        state = adapter.state
        if state.mapping_active:
            raise ConflictError("a mapping session is already running", source="adapters.g1")
        if adapter.mapping_launch is None:
            raise AdapterError(
                "mode.mapping_launch_file is not configured; mapping via API is unavailable",
                adapter="g1", operation="map.start_mapping", source="adapters.g1",
            )
        adapter.require_ros("map.start_mapping")

        def _start() -> bool:
            # A mapping session owns the SLAM stack: navigation goes down first.
            if adapter.launch is not None:
                adapter.launch.stop()
            adapter.mapping_launch.start()
            # map_builder_node is unique to the mapping stack (no stale-name
            # ambiguity with the localizer's topics).
            return adapter.ros.wait_node_alive("/map_builder_node", 60.0)

        ready = await _in_thread(_start)
        if not ready:
            await _in_thread(adapter.mapping_launch.stop)
            raise AdapterError(
                "mapping stack did not come up (map_builder_node) within 60s; "
                "see /tmp/g1_api_mapping_launch.log",
                adapter="g1", operation="map.start_mapping", source="adapters.g1",
            )
        state.localization_initialized = False
        state.mapping_active = True
        state.mapping_started_utc = time.time()
        return await self.mapping_status()

    async def mapping_status(self) -> MappingStatus:
        state = self._adapter.state
        if not state.mapping_active:
            return MappingStatus(active=False, note="idle")
        elapsed = time.time() - (state.mapping_started_utc or time.time())
        note = "mapping"
        if self._adapter.mapping_launch is not None:
            from g1_api.adapters.g1.launch_manager import LaunchStatus

            if self._adapter.mapping_launch.poll() == LaunchStatus.CRASHED:
                note = "mapping launch CRASHED; cancel and check the log"
        return MappingStatus(
            active=True, started_utc=state.mapping_started_utc, elapsed_s=elapsed, note=note
        )

    async def finish_mapping(self, map_id: str, name: str = "") -> MapManifest:
        adapter = self._adapter
        state = adapter.state
        if not state.mapping_active:
            raise ConflictError("no mapping session is running", source="adapters.g1")
        out_dir = os.path.expanduser(self._config.map.mapping_output_dir)
        t_begin = time.time()

        def _finish() -> None:
            import subprocess

            # 1. Save the 2D grid while octomap is still publishing.
            result = subprocess.run(
                ["rosrun", "map_server", "map_saver", "map:=/projected_map",
                 "-f", os.path.join(out_dir, "mymap")],
                capture_output=True, timeout=90,
            )
            if result.returncode != 0:
                raise AdapterError(
                    "map_saver failed: %s"
                    % (result.stderr or result.stdout or b"").decode(errors="replace")[-400:],
                    adapter="g1", operation="map.finish_mapping", source="adapters.g1",
                )
            # 2. Stop the mapping stack. roslaunch teardown SIGINTs children,
            #    which triggers map_builder's auto-save of map.pcd/ground_map.pcd.
            adapter.mapping_launch.stop()
            # 3. Wait for FRESH point clouds (the output dir may hold a
            #    previous session's files -- freshness is the check, not existence).
            deadline = time.monotonic() + 60.0
            needed = ("map.pcd", "ground_map.pcd")
            while time.monotonic() < deadline:
                try:
                    if all(
                        os.path.getsize(os.path.join(out_dir, n)) > 0
                        and os.path.getmtime(os.path.join(out_dir, n)) >= t_begin
                        for n in needed
                    ):
                        return
                except OSError:
                    pass
                time.sleep(1.0)
            raise AdapterError(
                "map.pcd / ground_map.pcd were not (re)written to %s within 60s "
                "after stopping the mapping stack" % out_dir,
                adapter="g1", operation="map.finish_mapping", source="adapters.g1",
            )

        await _in_thread(_finish)
        package = package_from_artifacts(out_dir, map_id, name, created_utc=time.time())
        state.mapping_active = False
        state.mapping_started_utc = None
        return await self.upload_map(package)

    async def cancel_mapping(self) -> bool:
        adapter = self._adapter
        state = adapter.state
        if not state.mapping_active:
            raise ConflictError("no mapping session is running", source="adapters.g1")
        if adapter.mapping_launch is not None:
            await _in_thread(adapter.mapping_launch.stop)
        state.mapping_active = False
        state.mapping_started_utc = None
        return True

    async def select_map(self, map_id: str) -> MapManifest:
        adapter = self._adapter
        if adapter.state.mapping_active:
            raise ConflictError(
                "a mapping session is running; finish or cancel it before selecting a map",
                source="adapters.g1",
            )
        manifest = adapter.state.maps.get(map_id)
        if manifest is None:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.g1")
        d = adapter.map_dir(map_id)
        missing = [n for n in self._REQUIRED_FILES if not os.path.isfile(os.path.join(d, n))]
        if missing:
            raise AdapterError(
                "map %s is missing file(s): %s" % (map_id, ", ".join(missing)),
                adapter="g1", operation="map.select_map", source="adapters.g1",
            )
        if adapter.launch is not None:
            # "grid_yaml", not "2dmap_file": a leading digit makes the
            # name:=value form parse as a remapping on the roslaunch CLI and
            # the arg silently keeps its default.
            args = {
                "pcd_path": os.path.join(d, "map.pcd"),
                "ground_pcd_path": os.path.join(d, "ground_map.pcd"),
                "grid_yaml": os.path.join(d, "grid.yaml"),
            }

            def _switch() -> bool:
                adapter.launch.stop()
                adapter.launch.set_args(args)
                adapter.launch.start()
                return adapter.ros.wait_nav_ready(self._config.mode.launch_ready_timeout_s)

            adapter.require_ros("map.select_map")
            ready = await _in_thread(_switch)
            if not ready:
                raise AdapterError(
                    "navigation launch did not become ready (move_base + /slam_reloc) "
                    "within %.0fs; see /tmp/g1_api_navigation_launch.log"
                    % self._config.mode.launch_ready_timeout_s,
                    adapter="g1", operation="map.select_map", source="adapters.g1",
                )
        adapter.state.current_map_id = map_id
        adapter.state.localization_initialized = False  # map switch requires relocalization
        return manifest

    async def delete_map(self, map_id: str) -> bool:
        state = self._adapter.state
        if map_id == state.current_map_id:
            raise ConflictError(
                "cannot delete the currently selected map; select another map first",
                source="adapters.g1",
            )
        if map_id not in state.maps:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.g1")
        del state.maps[map_id]
        d = self._adapter.map_dir(map_id)
        try:
            for name in os.listdir(d):
                os.remove(os.path.join(d, name))
            os.rmdir(d)
        except OSError:
            pass
        return True

    async def set_tour_points(self, map_id: str, tour_points) -> MapManifest:
        state = self._adapter.state
        manifest = state.maps.get(map_id)
        if manifest is None:
            raise NotFoundError(resource="map", identifier=map_id, source="adapters.g1")
        updated = MapManifest(
            map_id=manifest.map_id,
            name=manifest.name,
            created_utc=manifest.created_utc,
            resolution=manifest.resolution,
            origin=manifest.origin,
            tour_points=tuple(tour_points),
            checksums=manifest.checksums,
        )
        state.maps[map_id] = updated
        d = self._adapter.map_dir(map_id)
        try:
            with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as handle:
                json.dump(updated.to_dict(), handle, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return updated


class G1MotionAdapter(object):
    def __init__(self, adapter: "G1Adapter") -> None:
        self._adapter = adapter

    async def execute_action(self, record: ActionRecord, report_progress, cancel_event) -> ActionOutcome:
        adapter = self._adapter
        adapter.require_ros("motion.execute_action")
        kind = ActionKind(record.kind)

        # Bridge the asyncio cancel event to the blocking ROS thread.
        thread_cancel = threading.Event()

        async def _watch_cancel() -> None:
            await cancel_event.wait()
            thread_cancel.set()

        watcher = asyncio.ensure_future(_watch_cancel())
        try:
            if kind is ActionKind.MOVE:
                return await self._move(record.params, report_progress, thread_cancel)
            if kind is ActionKind.MOVE_TO:
                return await self._move_to(record.params, report_progress, thread_cancel)
            if kind in (ActionKind.ROTATE, ActionKind.ROTATE_TO):
                return await self._rotate(kind, record.params, report_progress, thread_cancel)
            return ActionOutcome.failed(FailureCode.UNKNOWN, "unknown action kind")
        finally:
            watcher.cancel()

    async def _move(self, params: MoveParams, report_progress, cancel: threading.Event) -> ActionOutcome:
        adapter = self._adapter
        report_progress(_progress(ActionStage.MOVING, 0.0))
        duration_s = params.duration_ms / 1000.0
        if adapter.dds_is_stub:
            completed = await _in_thread(
                adapter.ros.cmd_vel_pulse,
                params.vx_mps, params.vy_mps, params.omega_radps, duration_s, cancel,
            )
        else:
            def _drive() -> bool:
                loco = adapter.dds.loco
                deadline = time.monotonic() + duration_s
                loco.Move(params.vx_mps, params.vy_mps, params.omega_radps)
                interrupted = False
                while time.monotonic() < deadline:
                    if cancel.is_set():
                        interrupted = True
                        break
                    time.sleep(0.05)
                loco.StopMove()
                return not interrupted

            completed = await _in_thread(_drive)
        if not completed:
            return ActionOutcome.cancelled("move cancelled")
        report_progress(_progress(ActionStage.DONE, 1.0))
        return ActionOutcome.success("move completed")

    async def _move_to(self, params: MoveToParams, report_progress, cancel: threading.Event) -> ActionOutcome:
        adapter = self._adapter
        report_progress(_progress(ActionStage.GOING_TO_TARGET, 0.0))
        outcome, reason = await _in_thread(
            adapter.ros.nav_to_cancellable,
            params.target.x_m, params.target.y_m, params.yaw_rad, params.with_yaw,
            params.reach_threshold_m, params.timeout_s, cancel,
            getattr(params, "yaw_threshold_rad", 0.15),
            adapter.nav_feedback,
        )
        if outcome == "success":
            report_progress(_progress(ActionStage.DONE, 1.0))
            return ActionOutcome.success(reason)
        if outcome == "cancelled":
            return ActionOutcome.cancelled(reason)
        if outcome == "timeout":
            return ActionOutcome.failed(FailureCode.TIMEOUT, reason)
        return ActionOutcome.failed(FailureCode.GOAL_UNREACHABLE, reason)

    async def _rotate(self, kind: ActionKind, params: RotateParams, report_progress,
                      cancel: threading.Event) -> ActionOutcome:
        adapter = self._adapter
        report_progress(_progress(ActionStage.ROTATING, 0.0))
        if kind is ActionKind.ROTATE_TO:
            target_yaw = params.angle_rad
        else:
            pose = await _in_thread(adapter.ros.get_pose_tuple)
            if pose is None:
                return ActionOutcome.failed(
                    FailureCode.LOCALIZATION_NOT_READY, "TF map->body unavailable"
                )
            target_yaw = normalize_angle_rad(pose[3] + params.angle_rad)
        outcome, reason = await _in_thread(
            adapter.ros.rotate_to_cancellable, target_yaw, cancel
        )
        if outcome == "success":
            report_progress(_progress(ActionStage.DONE, 1.0))
            return ActionOutcome.success(reason)
        if outcome == "cancelled":
            return ActionOutcome.cancelled(reason)
        if outcome == "timeout":
            return ActionOutcome.failed(FailureCode.TIMEOUT, reason)
        return ActionOutcome.failed(FailureCode.UNKNOWN, reason)

    async def stop(self) -> None:
        if self._adapter.ros_available():
            await _in_thread(self._adapter.ros.stop)
        if not self._adapter.dds_is_stub and self._adapter.dds is not None:
            await _in_thread(self._adapter.dds.loco.StopMove)


class G1VoiceAdapter(object):
    def __init__(self, config: AppConfig, adapter: "G1Adapter") -> None:
        self._config = config
        self._adapter = adapter

    def _estimate(self, text: str, speaker_id: int = 0) -> float:
        return len(text) * self._config.voice.char_duration_for(speaker_id)

    async def speak(self, text: str, speaker_id: int = 0) -> float:
        audio = self._adapter.require_dds("voice.speak").audio
        code = await _in_thread(audio.TtsMaker, text, int(speaker_id))
        if code not in (0, None):
            raise AdapterError(
                "TTS failed with code %s" % code,
                adapter="g1", operation="voice.speak", source="adapters.g1",
            )
        return self._estimate(text, speaker_id)

    async def get_volume(self) -> int:
        audio = self._adapter.require_dds("voice.get_volume").audio
        result = await _in_thread(audio.GetVolume)
        try:
            code, data = result
            if code == 0 and isinstance(data, dict):
                return int(data.get("volume", 0))
            if isinstance(data, str):
                return int(json.loads(data).get("volume", 0))
        except Exception:  # noqa: BLE001
            pass
        raise AdapterError(
            "cannot read volume from AudioClient result %r" % (result,),
            adapter="g1", operation="voice.get_volume", source="adapters.g1",
        )

    async def set_volume(self, volume: int) -> int:
        vol = max(0, min(100, int(volume)))
        audio = self._adapter.require_dds("voice.set_volume").audio
        code = await _in_thread(audio.SetVolume, vol)
        if code not in (0, None):
            raise AdapterError(
                "SetVolume failed with DDS code %s" % code,
                adapter="g1", operation="voice.set_volume", source="adapters.g1",
            )
        return vol

    async def set_led(self, r: int, g: int, b: int) -> None:
        audio = self._adapter.require_dds("voice.set_led").audio
        code = await _in_thread(audio.LedControl, int(r), int(g), int(b))
        if code not in (0, None):
            raise AdapterError(
                "LedControl failed with DDS code %s (audio service reachable? "
                "LocoClient initialized before AudioClient?)" % code,
                adapter="g1", operation="voice.set_led", source="adapters.g1",
            )


class G1ArmAdapter(object):
    def __init__(self, adapter: "G1Adapter") -> None:
        self._adapter = adapter
        self._cached_actions: Optional[Dict[int, str]] = None

    async def get_action_list(self) -> Dict[int, str]:
        if self._cached_actions is not None:
            return dict(self._cached_actions)
        from g1_api.adapters.mock.adapter import DEFAULT_ARM_ACTIONS

        arm = self._adapter.require_dds("arm.get_action_list").arm
        actions: Dict[int, str] = {}
        try:
            raw = await _in_thread(arm.GetActionList)
            if isinstance(raw, dict):
                actions = {int(k): str(v) for k, v in raw.items()}
            elif isinstance(raw, str):
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    actions = {int(k): str(v) for k, v in parsed.items()}
        except Exception:  # noqa: BLE001
            actions = {}
        if not actions:
            actions = dict(DEFAULT_ARM_ACTIONS)
        self._cached_actions = actions
        return dict(actions)

    async def execute_action(self, action_id: int) -> None:
        arm = self._adapter.require_dds("arm.execute_action").arm
        await _in_thread(arm.ExecuteAction, int(action_id))


class G1HandAdapter(object):
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    async def send_command(self, cmd: str) -> str:
        ip = self._config.hand.robot_ip
        if not ip:
            raise AdapterError(
                "hand.robot_ip is not configured", adapter="g1", operation="hand.send_command",
                source="adapters.g1",
            )
        port = self._config.hand.port

        def _send() -> str:
            import socket  # real-hardware TCP; imported lazily so the module is import-clean

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(5.0)
                sock.connect((ip, port))
                sock.sendall(("%s\n" % cmd).encode())
                return sock.recv(4096).decode().strip()
            finally:
                sock.close()

        return await _in_thread(_send)


class G1Adapter(BaseRobotAdapter):
    adapter_name = "g1"

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self.state = G1State()
        self._dds: Optional[Any] = None
        self._ros: Optional[Any] = None
        self._launch: Optional[Any] = None
        self._mapping_launch: Optional[Any] = None
        super(G1Adapter, self).__init__(
            system=G1SystemAdapter(config, self),
            localization=G1LocalizationAdapter(self),
            map_adapter=G1MapAdapter(config, self),
            motion=G1MotionAdapter(self),
            voice=G1VoiceAdapter(config, self),
            arm=G1ArmAdapter(self),
            hand=G1HandAdapter(config),
        )

    # -------------------------------------------------------------- plumbing

    @property
    def dds(self) -> Any:
        return self._dds

    @property
    def dds_is_stub(self) -> bool:
        return self._config.mode.dds_backend == "stub"

    @property
    def dds_initialized(self) -> bool:
        return self._dds is not None

    @property
    def ros(self) -> Any:
        if self._ros is None:
            from g1_api.adapters.g1.ros_bridge import RosBridge

            self._ros = RosBridge()
        return self._ros

    @property
    def nav_feedback(self) -> str:
        return self._config.mode.nav_feedback

    @property
    def launch(self) -> Optional[Any]:
        return self._launch

    @property
    def mapping_launch(self) -> Optional[Any]:
        return self._mapping_launch

    def launch_crashed(self) -> bool:
        if self._launch is None:
            return False
        from g1_api.adapters.g1.launch_manager import LaunchStatus

        return self._launch.poll() == LaunchStatus.CRASHED

    def ros_available(self) -> bool:
        return self.ros.available()

    def require_ros(self, operation: str) -> Any:
        if not self.ros_available():
            raise AdapterError(
                "rospy is not importable in this environment; the real adapter's "
                "ROS features need a sourced ROS1 Noetic workspace",
                adapter="g1", operation=operation, source="adapters.g1",
            )
        return self.ros

    def require_dds(self, operation: str) -> Any:
        if self._dds is None:
            raise AdapterError(
                "DDS layer not initialized; call start() first",
                adapter="g1", operation=operation, source="adapters.g1",
            )
        return self._dds

    def map_dir(self, map_id: str) -> str:
        root = os.path.expanduser(self._config.map.map_storage_dir)
        return os.path.join(root, map_id)

    def _rescan_maps(self) -> None:
        root = os.path.expanduser(self._config.map.map_storage_dir)
        if not os.path.isdir(root):
            return
        for entry in sorted(os.listdir(root)):
            manifest_path = os.path.join(root, entry, "manifest.json")
            if not os.path.isfile(manifest_path):
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    manifest = MapManifest.from_dict(json.load(handle))
                self.state.maps[manifest.map_id] = manifest
            except Exception:  # noqa: BLE001 -- a broken dir must not kill startup
                continue

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        mode = self._config.mode
        if mode.dds_backend == "real":
            if not mode.network_interface:
                raise AdapterError(
                    "G1_NETWORK_INTERFACE (mode.network_interface) is required to start "
                    "the real adapter; refusing to guess between eth0/eno1/enp4s0.",
                    adapter="g1",
                    operation="start",
                    source="adapters.g1",
                )
            from g1_api.adapters.g1.dds_bridge import DdsBridge

            self._dds = DdsBridge(mode.network_interface)
        else:
            from g1_api.adapters.g1.dds_bridge import StubDdsBridge

            self._dds = StubDdsBridge()
        await _in_thread(self._dds.initialize)

        if mode.launch_file:
            from g1_api.adapters.g1.launch_manager import LaunchManager

            self._launch = LaunchManager(mode.launch_file, setup_file=mode.nav_launch_setup)
        if mode.mapping_launch_file:
            from g1_api.adapters.g1.launch_manager import LaunchManager

            self._mapping_launch = LaunchManager(
                mode.mapping_launch_file, log_path="/tmp/g1_api_mapping_launch.log"
            )

        self._rescan_maps()
        self._started = True

    async def stop(self) -> None:
        if self._mapping_launch is not None:
            await _in_thread(self._mapping_launch.stop)
        if self._launch is not None:
            await _in_thread(self._launch.stop)
        if self._dds is not None:
            self._dds.close()
        self._started = False
