"""The action lifecycle: submission, execution, cancellation.

One executor slot, exactly one action at a time (the Slamtec single-slot
design). A submission while something runs is refused with ``409 ROBOT_BUSY``.
Every submission passes through the safety gate before the adapter is touched.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from g1_api.errors import (
    FailureCode,
    NotFoundError,
    RobotBusyError,
    to_robot_error,
)
from g1_api.models.action import (
    ActionKind,
    ActionProgress,
    ActionRecord,
    ActionRequest,
)
from g1_api.models.enums import ActionLifecycle, ActionStage
from g1_api.adapters.base import ActionOutcome
from g1_api.core.clock import Clock
from g1_api.core.registry import ActionRegistry
from g1_api.core.safety import SafetyContext, SafetyGate, command_class_for_action_kind
from g1_api.state.action_state_machine import ActionStateMachine
from g1_api.utils.ids import new_action_id, new_uuid
from g1_api.utils.logging import get_logger

__all__ = ["TaskManager", "DEFAULT_HISTORY_SIZE"]

DEFAULT_HISTORY_SIZE = 100

_log = get_logger("core.task_manager")


class TaskManager(object):
    def __init__(
        self,
        adapter: Optional[Any] = None,
        safety_gate: Optional[SafetyGate] = None,
        registry: Optional[ActionRegistry] = None,
        clock: Optional[Clock] = None,
        events: Optional[Any] = None,
        safety_context_provider: Optional[Callable[[], Any]] = None,
        history_size: int = DEFAULT_HISTORY_SIZE,
        max_action_duration_s: float = 900.0,
        cancel_grace_s: float = 2.0,
    ) -> None:
        self._adapter = adapter
        self._gate = safety_gate if safety_gate is not None else SafetyGate()
        self._registry = registry
        self._clock = clock if clock is not None else Clock(name="task_manager")
        self._events = events
        self._context_provider = safety_context_provider
        self._history_size = max(1, int(history_size))
        self._max_action_duration_s = float(max_action_duration_s)
        self._cancel_grace_s = float(cancel_grace_s)

        self._records: "OrderedDict[str, ActionRecord]" = OrderedDict()
        self._machines: Dict[str, ActionStateMachine] = {}
        self._by_compat_id: Dict[int, str] = {}
        self._current_id: Optional[str] = None
        self._task: Optional[Any] = None
        self._cancel_events: Dict[str, Any] = {}
        self._running = False

    def set_adapter(self, adapter: Any) -> None:
        if self._current_id is not None:
            raise RobotBusyError("cannot swap the adapter while an action is running")
        self._adapter = adapter

    @property
    def current_id(self) -> Optional[str]:
        return self._current_id

    async def start(self) -> None:
        self._running = True

    async def stop(self, cancel_running: bool = True) -> None:
        self._running = False
        if cancel_running and self._current_id is not None:
            try:
                await self.cancel(self._current_id, reason="task manager shutting down")
            except Exception:  # noqa: BLE001
                _log.exception("failed to cancel the running action during shutdown")

    async def submit(
        self,
        request: ActionRequest,
        requester: Optional[str] = None,
        context: Optional[SafetyContext] = None,
    ) -> ActionRecord:
        if not self._running:
            await self.start()

        caller = requester if requester is not None else request.requester
        kind = ActionKind(request.kind)

        if self._registry is not None:
            self._registry.validate_params(kind, request.params)

        ctx = await self._build_context(request, caller, context)
        decision = self._gate.check(command_class_for_action_kind(kind), ctx)
        if not decision.allowed:
            raise decision.to_error()

        busy = self._active_record()
        if busy is not None:
            raise RobotBusyError(
                "action %s (%s) is already running; cancel it or wait"
                % (busy.id, busy.kind),
                running_action_id=busy.id,
                source="core.task_manager",
                details={"running_action_kind": str(busy.kind), "running_compat_id": busy.compat_id},
            )

        record = self._create_record(request, caller)
        self._launch(record.id)
        return self._records[record.id]

    def get(self, action_id: str) -> ActionRecord:
        record = self._records.get(str(action_id))
        if record is None:
            raise NotFoundError(resource="action", identifier=action_id, source="core.task_manager")
        return record

    def get_by_compat_id(self, compat_id: int) -> ActionRecord:
        action_id = self._by_compat_id.get(int(compat_id))
        if action_id is None:
            raise NotFoundError(resource="action", identifier=compat_id, source="core.task_manager")
        return self.get(action_id)

    def current(self) -> Optional[ActionRecord]:
        return self._active_record()

    def list(self, limit: Optional[int] = None) -> Tuple[ActionRecord, ...]:
        records = list(self._records.values())
        if limit is not None:
            records = records[-int(limit):]
        return tuple(records)

    async def cancel(self, action_id: str, reason: str = "cancelled by request") -> ActionRecord:
        record = self.get(action_id)
        if record.is_terminal:
            return record
        if self._current_id != action_id:
            return self._finalise(action_id, ActionLifecycle.CANCELLED, FailureCode.CANCELLED_BY_USER, reason)

        event = self._cancel_events.get(action_id)
        if event is not None:
            event.set()
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._cancel_grace_s)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=self._cancel_grace_s)
                except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
                    pass
            except asyncio.CancelledError:  # pragma: no cover
                pass
        return self._records.get(action_id, record)

    async def cancel_current(self, reason: str = "cancelled by request") -> ActionRecord:
        record = self._active_record()
        if record is None:
            raise NotFoundError("Action Not Found", resource="action", identifier="current", source="core.task_manager")
        return await self.cancel(record.id, reason=reason)

    def _launch(self, action_id: str) -> None:
        self._current_id = action_id
        self._cancel_events[action_id] = asyncio.Event()
        self._task = asyncio.ensure_future(self._run(action_id))

    async def _run(self, action_id: str) -> None:
        record = self._records[action_id]
        cancel_event = self._cancel_events[action_id]
        machine = self._machine(action_id)
        try:
            machine.start()
            record = self._store(
                record.transition(ActionLifecycle.RUNNING, self._clock.stamp())
            )
            self._publish("action_started", record)

            outcome = await self._execute(record, cancel_event)
        except asyncio.CancelledError:
            cancel_event.set()
            self._finalise(action_id, ActionLifecycle.CANCELLED, FailureCode.CANCELLED_BY_USER, "execution task cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            error = to_robot_error(exc, source="adapters")
            self._finalise(action_id, ActionLifecycle.FAILED, error.failure_code or FailureCode.UNKNOWN, error.message)
        else:
            self._finalise(action_id, outcome.lifecycle, outcome.failure_code, outcome.reason, progress=outcome.progress)
        finally:
            if self._current_id == action_id:
                self._current_id = None
                self._task = None
            self._cancel_events.pop(action_id, None)

    async def _execute(self, record: ActionRecord, cancel_event: Any) -> ActionOutcome:
        motion = self._motion_adapter()
        deadline = self._deadline_for(record)
        report = self._progress_reporter(record.id)
        task = asyncio.ensure_future(motion.execute_action(record, report, cancel_event))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=deadline)
        except asyncio.TimeoutError:
            cancel_event.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._cancel_grace_s)
            except asyncio.TimeoutError:
                task.cancel()
            except asyncio.CancelledError:  # pragma: no cover
                pass
            return ActionOutcome.failed(FailureCode.TIMEOUT, "exceeded the %.1f s deadline" % (deadline,))

    def _deadline_for(self, record: ActionRecord) -> float:
        candidates = [self._max_action_duration_s]
        params = record.params
        own_bound = getattr(params, "timeout_s", None) if params is not None else None
        if own_bound:
            candidates.append(float(own_bound))
        if params is not None and getattr(params, "duration_ms", None):
            candidates.append(float(params.duration_ms) / 1000.0 + 5.0)
        return min(candidates)

    def _progress_reporter(self, action_id: str) -> Callable[[ActionProgress], None]:
        def report(progress: ActionProgress) -> None:
            record = self._records.get(action_id)
            if record is None or record.is_terminal:
                return
            self._store(record.transition(record.lifecycle, self._clock.stamp(), progress=progress))
        return report

    def _create_record(self, request: ActionRequest, requester: Optional[str]) -> ActionRecord:
        stamp = self._clock.stamp()
        record = ActionRecord(
            id=new_uuid(),
            compat_id=new_action_id(),
            kind=ActionKind(request.kind),
            lifecycle=ActionLifecycle.PENDING,
            progress=ActionProgress(stage=ActionStage.IDLE),
            created_at=stamp,
            updated_at=stamp,
            params=request.params,
            requester=requester,
        )
        self._records[record.id] = record
        self._machines[record.id] = ActionStateMachine(action_id=record.id)
        self._by_compat_id[record.compat_id] = record.id
        self._trim_history()
        self._publish("action_created", record)
        return record

    def _finalise(
        self,
        action_id: str,
        lifecycle: ActionLifecycle,
        failure_code: Optional[FailureCode],
        reason: str,
        progress: Optional[ActionProgress] = None,
    ) -> ActionRecord:
        record = self._records.get(action_id)
        if record is None:
            raise NotFoundError(resource="action", identifier=action_id)
        if record.is_terminal:
            return record
        machine = self._machine(action_id)
        target = ActionLifecycle(lifecycle)
        if target is ActionLifecycle.SUCCEEDED:
            machine.succeed(reason or "")
        elif target is ActionLifecycle.CANCELLED:
            machine.cancel(reason or "cancelled")
        else:
            machine.fail(failure_code or FailureCode.UNKNOWN, reason or "")
        updated = self._store(
            record.transition(
                machine.lifecycle,
                self._clock.stamp(),
                result=machine.result,
                failure_code=machine.failure_code,
                reason=machine.reason,
                progress=progress if progress is not None else record.progress,
            )
        )
        event_type = {
            ActionLifecycle.SUCCEEDED: "action_succeeded",
            ActionLifecycle.FAILED: "action_failed",
            ActionLifecycle.CANCELLED: "action_cancelled",
        }.get(machine.lifecycle, "action_failed")
        self._publish(event_type, updated)
        return updated

    def _store(self, record: ActionRecord) -> ActionRecord:
        self._records[record.id] = record
        return record

    def _machine(self, action_id: str) -> ActionStateMachine:
        machine = self._machines.get(str(action_id))
        if machine is None:
            raise NotFoundError(resource="action", identifier=action_id, source="core.task_manager")
        return machine

    def _active_record(self) -> Optional[ActionRecord]:
        if self._current_id is None:
            return None
        record = self._records.get(self._current_id)
        if record is None or record.is_terminal:
            return None
        return record

    def _trim_history(self) -> None:
        while len(self._records) > self._history_size:
            for action_id, record in list(self._records.items()):
                if record.is_terminal:
                    self._records.pop(action_id, None)
                    self._machines.pop(action_id, None)
                    self._by_compat_id.pop(record.compat_id, None)
                    break
            else:
                return

    def _motion_adapter(self) -> Any:
        if self._adapter is None:
            raise NotFoundError("no hardware adapter is bound", resource="adapter", source="core.task_manager")
        return self._adapter.motion

    async def _build_context(
        self, request: ActionRequest, caller: Optional[str], context: Optional[SafetyContext]
    ) -> SafetyContext:
        base = context
        if base is None and self._context_provider is not None:
            produced = self._context_provider()
            if inspect.isawaitable(produced):
                produced = await produced
            base = produced
        if base is None:
            base = SafetyContext()
        return base.with_(
            caller=caller,
            description="submit action %s" % (request.kind,),
            action_kind=ActionKind(request.kind),
        )

    def _publish(self, event_type: str, record: ActionRecord) -> None:
        if self._events is None:
            return
        payload = {
            "action_id": record.id,
            "compat_id": record.compat_id,
            "action_name": record.action_name,
            "state": str(record.lifecycle),
        }
        if record.failure_code is not None:
            payload["failure_code"] = str(record.failure_code)
        if record.reason:
            payload["reason"] = record.reason
        try:
            self._events.publish(
                "action", event_type, payload=payload, source="core.task_manager", stamp=self._clock.stamp()
            )
        except Exception:  # pragma: no cover
            _log.exception("failed to publish %s for action %s", event_type, record.id)

    def to_dict(self) -> Dict[str, Any]:
        current = self._active_record()
        return {
            "current": None if current is None else current.to_dict(),
            "records": len(self._records),
            "history_size": self._history_size,
        }

    def __repr__(self) -> str:
        return "TaskManager(current=%r, history=%d)" % (self._current_id, len(self._records))
