"""The robot core: one owner of clock, event bus, safety gate, registries, task
manager and the adapter. The safety context provider reads live adapter state."""

from __future__ import annotations

from typing import Any, Optional

from g1_api.adapters.base import RobotAdapter
from g1_api.config import AppConfig
from g1_api.core.clock import Clock
from g1_api.core.event_bus import EventBus
from g1_api.core.registry import build_default_action_registry
from g1_api.core.safety import SafetyContext, SafetyGate
from g1_api.core.task_manager import TaskManager

__all__ = ["RobotCore"]


class RobotCore(object):
    def __init__(
        self,
        config: AppConfig,
        adapter: RobotAdapter,
        clock: Optional[Clock] = None,
    ) -> None:
        self._config = config
        self.adapter = adapter
        self.clock = clock if clock is not None else Clock(name="core")
        self.events = EventBus(clock=self.clock)
        self.safety_gate = SafetyGate(config=config.safety, clock=self.clock)
        self.registry = build_default_action_registry()
        self._tour_running = False
        self._external_motion_running = False

        self.task_manager = TaskManager(
            adapter=adapter,
            safety_gate=self.safety_gate,
            registry=self.registry,
            clock=self.clock,
            events=self.events,
            safety_context_provider=self.safety_context,
            max_action_duration_s=config.safety.max_action_duration_s,
        )

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def tour_running(self) -> bool:
        return self._tour_running

    def set_tour_running(self, running: bool) -> None:
        self._tour_running = bool(running)

    @property
    def external_motion_running(self) -> bool:
        return self._external_motion_running

    def set_external_motion_running(self, running: bool) -> None:
        self._external_motion_running = bool(running)

    async def safety_context(self, **overrides: Any) -> SafetyContext:
        """Build a SafetyContext from the adapter's live safety status."""
        status = await self.adapter.system.get_safety_status()
        ctx = SafetyContext(
            adapter_ready=self.adapter.ready(),
            estop_engaged=status.estop_engaged,
            roscore_down=status.roscore_down,
            move_base_down=status.move_base_down,
            velocity_bridge_down=status.velocity_bridge_down,
            dds_unreachable=status.dds_unreachable,
            map_not_selected=status.map_not_selected,
            localization_not_initialized=status.localization_not_initialized,
            tour_running=self._tour_running,
            mapping_running=status.mapping_running,
            external_motion_running=self._external_motion_running,
        )
        return ctx.with_(**overrides) if overrides else ctx

    async def start(self) -> None:
        await self.adapter.start()
        await self.task_manager.start()

    async def stop(self) -> None:
        await self.task_manager.stop(cancel_running=True)
        await self.adapter.stop()

    def ready(self) -> bool:
        return self.adapter.ready()

    def __repr__(self) -> str:
        return "RobotCore(adapter=%r, ready=%s)" % (self.adapter.name, self.adapter.ready())
