"""Tour service: the tour definition (points in the map manifest) and control.

Tour points travel with the map (in its manifest); the tour-level ``on_failure``
and ``loop`` are in-memory application config held here, set by ``PUT /tours``.
"""

from __future__ import annotations

from typing import Optional

from g1_api.core.core import RobotCore
from g1_api.errors import SafetyInterlockError
from g1_api.models.tour import Tour, TourPoint, TourState
from g1_api.tour.executor import TourExecutor


class TourService(object):
    def __init__(self, core: RobotCore, executor: TourExecutor) -> None:
        self._core = core
        self._executor = executor
        self._on_failure = "retry_once"
        self._loop = False

    async def get_tour(self) -> Optional[Tour]:
        current = await self._core.adapter.map.get_current_map()
        if current is None:
            return None
        return Tour(
            tour_id=current.map_id,
            map_id=current.map_id,
            points=tuple(TourPoint(**p.to_dict()) for p in current.tour_points),
            loop=self._loop,
            on_failure=self._on_failure,
        )

    async def put_tour(self, tour: Tour, caller: Optional[str] = None) -> Tour:
        current = await self._core.adapter.map.get_current_map()
        if current is None:
            raise SafetyInterlockError(
                "no map selected", interlocks=["map_not_selected"], source="services.tour_service"
            )
        manifest = await self._core.adapter.map.set_tour_points(current.map_id, tour.points)
        self._on_failure = tour.on_failure
        self._loop = tour.loop
        return Tour(
            tour_id=manifest.map_id,
            map_id=manifest.map_id,
            points=tuple(TourPoint(**p.to_dict()) for p in manifest.tour_points),
            loop=self._loop,
            on_failure=self._on_failure,
        )

    async def start(self, caller: Optional[str] = None) -> TourState:
        tour = await self.get_tour()
        if tour is None or not tour.points:
            raise SafetyInterlockError(
                "no tour points defined for the current map",
                interlocks=["map_not_selected"],
                source="services.tour_service",
            )
        return await self._executor.start(tour, caller=caller)

    async def test_point(self, index: int, with_nav: bool = False,
                         caller: Optional[str] = None) -> TourState:
        """Rehearse ONE tour point through the real executor (so :current /
        :stop / events behave identically).

        Default ``with_nav=False``: in-place rehearsal -- the MoveTo leg is
        skipped entirely (no stepping, no navigation-readiness or localization
        required) and only actions/TTS/dwell run. ``with_nav=True`` walks
        there first (full pipeline)."""
        tour = await self.get_tour()
        if tour is None or not tour.points:
            raise SafetyInterlockError(
                "no tour points defined for the current map",
                interlocks=["map_not_selected"],
                source="services.tour_service",
            )
        if not (0 <= int(index) < len(tour.points)):
            from g1_api.errors import NotFoundError

            raise NotFoundError(resource="tour point", identifier=index,
                                source="services.tour_service")
        single = Tour(
            tour_id="%s#test-%d%s" % (tour.map_id, int(index), "" if with_nav else "-inplace"),
            map_id=tour.map_id,
            points=(tour.points[int(index)],),
            loop=False,
            on_failure="abort",
            skip_nav=not with_nav,
        )
        return await self._executor.start(single, caller=caller)

    async def pause(self) -> TourState:
        return await self._executor.pause()

    async def resume(self) -> TourState:
        return await self._executor.resume()

    async def stop(self) -> TourState:
        return await self._executor.stop()

    def current(self) -> TourState:
        return self._executor.state
