"""The service registry: one object the gateway depends on."""

from __future__ import annotations

from typing import Optional

from g1_api.core.core import RobotCore
from g1_api.services.arm import ArmService
from g1_api.services.external_actions import ExternalActionService
from g1_api.services.hand import HandService
from g1_api.services.localization import LocalizationService
from g1_api.services.map import MapService
from g1_api.services.motion import MotionService
from g1_api.services.system import SystemService
from g1_api.services.voice import VoiceService
from g1_api.services.tour_service import TourService
from g1_api.tour.executor import TourExecutor


class ServiceRegistry(object):
    def __init__(self, core: RobotCore) -> None:
        self.core = core
        self.tour_executor = TourExecutor(core)
        self.motion = MotionService(core)
        self.map = MapService(core)
        self.localization = LocalizationService(core)
        self.system = SystemService(core)
        self.voice = VoiceService(core)
        self.arm = ArmService(core)
        self.external_actions = ExternalActionService(core)
        self.hand = HandService(core)
        self.tour = TourService(core, self.tour_executor)

    async def start(self) -> None:
        await self.core.start()

    async def stop(self) -> None:
        await self.core.stop()
