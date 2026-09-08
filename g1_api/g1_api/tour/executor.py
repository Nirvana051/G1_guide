"""The tour executor: orchestrates a sequence of ``MoveToAction`` legs.

The tour layer **eats its own dog food**: every leg is submitted through the
internal core action API (``task_manager.submit``), never by touching the
adapter directly. At each point it runs the arm/hand action sequence and the
TTS in parallel (``asyncio.gather``), then dwells.

Blocked-path handling mirrors the reference ``multi_nav.py`` strategy in spirit:
the mock simulates it as an injected ``GOAL_UNREACHABLE`` outcome, which is
what the ``on_failure`` policy acts on.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from g1_api.core.core import RobotCore
from g1_api.core.safety import CommandClass
from g1_api.models.action import ActionRequest
from g1_api.models.enums import ActionKind
from g1_api.models.tour import Tour, TourPoint, TourPointResult, TourState
from g1_api.models.state import Interlock
from g1_api.errors import SafetyInterlockError

__all__ = ["TourExecutor"]


class TourExecutor(object):
    def __init__(self, core: RobotCore) -> None:
        self._core = core
        self._tour: Optional[Tour] = None
        self._state = TourState()
        self._results: List[TourPointResult] = []
        self._task: Optional[asyncio.Task] = None
        self._pause_event = asyncio.Event()
        self._stop_event = asyncio.Event()

    @property
    def state(self) -> TourState:
        return self._state

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, tour: Tour, caller: Optional[str] = None) -> TourState:
        if self.is_running():
            raise SafetyInterlockError(
                "a tour is already running", interlocks=[Interlock.TOUR_RUNNING],
                source="tour.executor",
            )
        # Pre-flight: the tour flag always; the navigation preconditions (map
        # selected, localized, allow_navigation) only when the tour actually
        # walks -- an in-place rehearsal (skip_nav) runs actions/TTS only and
        # must not demand localization.
        ctx = await self._core.safety_context(caller=caller, description="start tour")
        classes = (CommandClass.TOUR,) if tour.skip_nav else (CommandClass.TOUR, CommandClass.NAVIGATION)
        for klass in classes:
            decision = self._core.safety_gate.check(klass, ctx)
            if not decision.allowed:
                raise decision.to_error()

        self._tour = tour
        self._results = [
            TourPointResult(index=i, name=p.name, status="pending")
            for i, p in enumerate(tour.points)
        ]
        self._state = TourState(
            status="running", tour_id=tour.tour_id, map_id=tour.map_id,
            current_index=0, point_results=tuple(self._results),
        )
        self._pause_event.clear()
        self._stop_event.clear()
        self._task = asyncio.ensure_future(self._run())
        return self._state

    async def pause(self) -> TourState:
        if not self.is_running():
            raise SafetyInterlockError("no tour is running", source="tour.executor")
        self._pause_event.set()
        self._state = _replace_status(self._state, "paused")
        return self._state

    async def resume(self) -> TourState:
        self._pause_event.clear()
        self._state = _replace_status(self._state, "running")
        return self._state

    async def stop(self) -> TourState:
        self._stop_event.set()
        # Cancel the running leg's action, then stop the robot.
        try:
            await self._core.task_manager.cancel_current(reason="tour stopped")
        except Exception:
            pass
        try:
            await self._core.adapter.motion.stop()
        except Exception:
            pass
        # Wait for the executor task to settle so the next start() sees it done.
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        self._state = _replace_status(self._state, "stopped")
        return self._state

    async def wait(self, timeout_s: float = 60.0) -> TourState:
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_s)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass
        return self._state

    # -- internals -------------------------------------------------------

    async def _run(self) -> None:
        self._core.set_tour_running(True)
        try:
            for index, point in enumerate(self._tour.points):
                await self._await_if_paused()
                if self._stop_event.is_set():
                    break
                self._state = _replace_status(self._state, "running", index)
                try:
                    result = await self._run_point(index, point)
                except Exception as exc:  # noqa: BLE001 - a gate denial or adapter fault
                    result = TourPointResult(
                        index=index, name=point.name, status="failed",
                        reason="%s: %s" % (type(exc).__name__, exc),
                    )
                    self._publish("point_failed", index=index, name=point.name, reason=result.reason)
                self._results[index] = result
                self._state = _replace_status(
                    self._state, "running", index, tuple(self._results)
                )
                if result.status == "failed":  # abort
                    self._state = _replace_status(
                        self._state, "failed", index, tuple(self._results)
                    )
                    break
            else:
                if not self._stop_event.is_set():
                    self._state = _replace_status(
                        self._state, "finished", len(self._tour.points), tuple(self._results)
                    )
        finally:
            self._core.set_tour_running(False)
            if self._stop_event.is_set() and self._state.status == "running":
                self._state = _replace_status(self._state, "stopped", tuple(self._results))

    async def _run_point(self, index: int, point: TourPoint) -> TourPointResult:
        attempts = 2 if self._tour.on_failure == "retry_once" else 1
        for attempt in range(attempts):
            await self._await_if_paused()
            if self._stop_event.is_set():
                return TourPointResult(index=index, name=point.name, status="failed", attempts=attempt, reason="stopped")
            ok = True if self._tour.skip_nav else await self._navigate_to_point(point)
            if ok:
                await asyncio.gather(
                    self._run_actions(point),
                    self._run_tts(point),
                )
                if point.dwell_s:
                    await self._sleep_checked(point.dwell_s)
                self._publish("point_reached", index=index, name=point.name)
                return TourPointResult(index=index, name=point.name, status="succeeded", attempts=attempt + 1)

        # all attempts failed
        self._publish("point_failed", index=index, name=point.name)
        if self._tour.on_failure == "skip":
            return TourPointResult(index=index, name=point.name, status="skipped", attempts=attempts, reason="navigation failed")
        return TourPointResult(index=index, name=point.name, status="failed", attempts=attempts, reason="navigation failed")

    async def _navigate_to_point(self, point: TourPoint) -> bool:
        request = ActionRequest(
            kind=ActionKind.MOVE_TO,
            params=_move_to_params(point),
            requester="tour",
        )
        # The tour's own leg must not be refused by the tour_running interlock.
        ctx = await self._core.safety_context()
        ctx = ctx.with_(tour_running=False, caller="tour", description="tour leg")
        record = await self._core.task_manager.submit(request, requester="tour", context=ctx)
        while True:
            await self._await_if_paused()
            if self._stop_event.is_set():
                try:
                    await self._core.task_manager.cancel(record.id, reason="tour stopped")
                except Exception:
                    pass
                return False
            current = self._core.task_manager.get(record.id)
            if current.is_terminal:
                return current.succeeded
            await asyncio.sleep(0.05)

    async def _run_actions(self, point: TourPoint) -> None:
        # Release (action 99 + settle) only ONCE, after the LAST onboard arm
        # action of the sequence -- releasing between chained arm actions just
        # wastes a reset + 3 s settle per action (course habit, dropped).
        last_arm_index = None
        for i, a in enumerate(point.actions):
            if a.get("executor", "onboard") == "onboard" and a.get("type") == "arm":
                last_arm_index = i
        action_index = -1
        actions = list(point.actions)
        while action_index + 1 < len(actions):
            action_index += 1
            action = actions[action_index]
            if self._stop_event.is_set():
                return
            # Executor routing: "onboard" (default) runs through the robot's own
            # pipelines below; "external" goes to the user's replay executor.
            # CONSECUTIVE external replays are batched into ONE session: a lone
            # replay subprocess per action would hand the arms back to the loco
            # service between actions (weight ramp 1->0->1 = visible reset);
            # batched, the weight stays up and release happens once at the end.
            if action.get("executor", "onboard") == "external":
                group = [action]
                while (action_index + 1 < len(actions)
                       and actions[action_index + 1].get("executor", "onboard") == "external"):
                    action_index += 1
                    group.append(actions[action_index])
                await self._run_external_group(point, group)
                continue
            if action.get("type") == "arm":
                action_id = int(action["id"])
                # Every state change passes the safety gate -- the tour's own
                # actions included. The tour already passed the TOUR gate at
                # start; here it re-checks ARM with the tour_running interlock
                # explicitly cleared, so the interlock refuses *manual* commands
                # but never the tour's own.
                await self._authorized_check(CommandClass.ARM, "tour arm action %d" % action_id)
                await self._core.adapter.arm.execute_action(action_id)
                if action_index == last_arm_index:
                    await self._core.adapter.arm.execute_action(
                        self._core.config.tour.arm_release_action_id
                    )
                    await self._sleep_checked(self._core.config.tour.arm_release_wait_s)
                self._publish("action_done", name=point.name, action=action)
            elif action.get("type") == "hand":
                await self._authorized_check(CommandClass.HAND, "tour hand command")
                await self._core.adapter.hand.send_command(str(action["cmd"]))
                self._publish("action_done", name=point.name, action=action)

    async def _run_external_group(self, point: TourPoint, group: List[Any]) -> None:
        """Run a run of consecutive external actions. When ALL of them resolve
        to library replays and there is more than one, they are sent as a
        single ``replay_sequence`` job (one arm_sdk session, no hand-back to
        the loco service between trajectories); otherwise fall back to
        one-by-one execution."""
        if len(group) < 2:
            await self._run_external_action(point, group[0])
            return
        url = self._core.config.tour.external_executor_url
        if not url:
            for action in group:
                await self._run_external_action(point, action)  # publishes skipped
            return
        from g1_api.models.external_action import build_execute_payload, library_load

        directory = self._core.config.tour.external_actions_dir
        items = []
        for action in group:
            candidate = str(action.get("id") or "")
            if not candidate and action.get("type") not in (None, "", "replay"):
                candidate = str(action["type"])
            meta = library_load(directory, candidate) if candidate else None
            if meta is None:
                items = None
                break
            items.append(build_execute_payload(meta, directory))
        if items is None:  # mixed / custom types: no batching
            for action in group:
                await self._run_external_action(point, action)
            return

        from g1_api.utils.external_executor import post_json

        total = sum(float(it["duration_s"]) for it in items)
        wire_action = {
            "executor": "external",
            "type": "replay_sequence",
            "items": items,
            "duration_s": total,
        }
        timeout_s = total + float(self._core.config.tour.external_execute_timeout_margin_s)

        def _post() -> int:
            return post_json(url, {"point": point.to_dict(), "action": wire_action},
                             timeout_s=timeout_s)

        self._core.set_external_motion_running(True)
        try:
            code = await asyncio.get_event_loop().run_in_executor(None, _post)
            self._publish("action_done", name=point.name,
                          action={"executor": "external", "type": "replay_sequence",
                                  "ids": [it["id"] for it in items]},
                          external_status=code)
        except Exception as exc:  # noqa: BLE001 -- never kills the tour
            self._publish(
                "external_action_failed", name=point.name,
                action={"type": "replay_sequence", "ids": [it["id"] for it in items]},
                reason=str(exc),
            )
        finally:
            self._core.set_external_motion_running(False)

    async def _run_external_action(self, point: TourPoint, action: Any) -> None:
        url = self._core.config.tour.external_executor_url
        if not url:
            self._publish(
                "external_action_skipped", name=point.name, action=dict(action),
                reason="tour.external_executor_url is not configured",
            )
            return
        from g1_api.utils.external_executor import post_json

        wire_action = dict(action)
        timeout_s = 15.0
        # Library resolution: a replay reference may arrive as
        # {"type": "replay", "id": "e1"} (canonical) or {"type": "e1"} (what
        # the map editor's free-form type field naturally produces). Either
        # way, if it names a library entry the POSTed action is enriched with
        # the file path / frequency / limits so the replay server needs no
        # library knowledge, and the timeout follows the trajectory duration.
        # An unresolved non-replay type passes through untouched -- that is
        # the seam for genuinely custom executors.
        from g1_api.models.external_action import build_execute_payload, library_load

        directory = self._core.config.tour.external_actions_dir
        candidate = str(wire_action.get("id") or "")
        if not candidate and wire_action.get("type") not in (None, "", "replay"):
            candidate = str(wire_action["type"])
        meta = library_load(directory, candidate) if candidate else None
        if meta is not None:
            wire_action = build_execute_payload(meta, directory)
            timeout_s = meta.duration_s + float(
                self._core.config.tour.external_execute_timeout_margin_s)
        elif wire_action.get("type") == "replay":
            self._publish(
                "external_action_failed", name=point.name, action=wire_action,
                reason="external action %r is not in the library" % (candidate or "<no id>",),
            )
            return

        def _post() -> int:
            return post_json(url, {"point": point.to_dict(), "action": wire_action},
                             timeout_s=timeout_s)

        # Hold the external_motion_running interlock while the replay owns the
        # arms, so parallel manual arm / navigation requests are refused.
        self._core.set_external_motion_running(True)
        try:
            code = await asyncio.get_event_loop().run_in_executor(None, _post)
            self._publish("action_done", name=point.name, action=dict(action), external_status=code)
        except Exception as exc:  # noqa: BLE001 -- an external executor failure never kills the tour
            self._publish(
                "external_action_failed", name=point.name, action=dict(action), reason=str(exc)
            )
        finally:
            self._core.set_external_motion_running(False)

    async def _run_tts(self, point: TourPoint) -> None:
        if not point.tts_text:
            return
        self._publish("tts_started", name=point.name)
        await self._authorized_check(CommandClass.VOICE, "tour tts")
        est = await self._core.adapter.voice.speak(
            point.tts_text, speaker_id=int(self._core.config.voice.default_speaker_id))
        # Manual per-point override beats the estimate (the G1 has no TTS
        # completion callback; the estimate is chars * per-speaker coefficient).
        manual = float(getattr(point, "tts_duration_s", 0.0) or 0.0)
        wait = manual if manual > 0 else est + self._core.config.voice.completion_margin_s
        await self._sleep_checked(wait)

    async def _authorized_check(self, command_class: CommandClass, description: str) -> None:
        """Run a command through the safety gate with the tour_running interlock
        cleared -- the tour's own commands are authorized, manual ones are not.

        There is no side channel: this is the same ``SafetyGate.check`` every
        other state change uses; only the ``tour_running`` flag is overridden.
        """
        ctx = await self._core.safety_context()
        ctx = ctx.with_(tour_running=False, caller="tour", description=description)
        decision = self._core.safety_gate.check(command_class, ctx)
        if not decision.allowed:
            raise decision.to_error()

    async def _sleep_checked(self, seconds: float) -> None:
        deadline = asyncio.get_event_loop().time() + seconds
        while True:
            await self._await_if_paused()
            if self._stop_event.is_set():
                return
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.05, remaining))

    async def _await_if_paused(self) -> None:
        while self._pause_event.is_set() and not self._stop_event.is_set():
            await asyncio.sleep(0.05)

    def _publish(self, event_type: str, **payload: Any) -> None:
        try:
            self._core.events.publish("tour", event_type, payload=payload, source="tour.executor")
        except Exception:
            pass


def _move_to_params(point: TourPoint) -> Any:
    from g1_api.models.action import MoveToParams
    from g1_api.models.geometry import Point2D

    return MoveToParams(
        target=Point2D(point.x, point.y),
        yaw_rad=point.yaw,
        with_yaw=True,
        reach_threshold_m=point.reach_threshold,
        yaw_threshold_rad=point.yaw_threshold,
        timeout_s=120.0,
    )


def _replace_status(state: TourState, status: str, index: Optional[int] = None, results: Optional[Tuple[TourPointResult, ...]] = None) -> TourState:
    return TourState(
        status=status,
        tour_id=state.tour_id,
        map_id=state.map_id,
        current_index=state.current_index if index is None else index,
        point_results=state.point_results if results is None else results,
    )
