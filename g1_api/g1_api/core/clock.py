"""The single time authority."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from g1_api.models.geometry import Stamp
from g1_api.utils.ids import new_uuid

__all__ = ["Clock", "ManualClock", "NS_PER_MS", "NS_PER_S"]

NS_PER_MS = 1000000
NS_PER_S = 1000000000


class Clock(object):
    def __init__(
        self,
        mono_source: Optional[Callable[[], int]] = None,
        utc_source: Optional[Callable[[], int]] = None,
        boot_id: Optional[str] = None,
        name: str = "clock",
    ) -> None:
        self._mono_source = mono_source if mono_source is not None else time.monotonic_ns
        self._utc_source = utc_source if utc_source is not None else time.time_ns
        self._boot_id = str(boot_id) if boot_id else new_uuid()
        self._name = str(name)
        self._boot_mono_ns = int(self._mono_source())
        self._boot_utc_ns = int(self._utc_source())

    @property
    def boot_id(self) -> str:
        return self._boot_id

    @property
    def name(self) -> str:
        return self._name

    def mono_ns(self) -> int:
        return int(self._mono_source())

    def utc_ns(self) -> int:
        return int(self._utc_source())

    def mono_s(self) -> float:
        return self.mono_ns() / float(NS_PER_S)

    def utc_s(self) -> float:
        return self.utc_ns() / float(NS_PER_S)

    def uptime_s(self) -> float:
        return (self.mono_ns() - self._boot_mono_ns) / float(NS_PER_S)

    def stamp(self) -> Stamp:
        return Stamp(mono_ns=self.mono_ns(), utc_ns=self.utc_ns())

    def age_s(self, stamp: Stamp) -> float:
        return (self.mono_ns() - int(stamp.mono_ns)) / float(NS_PER_S)

    def to_dict(self) -> Dict[str, Any]:
        stamp = self.stamp()
        return {
            "t_mono_ns": stamp.mono_ns,
            "t_utc_ns": stamp.utc_ns,
            "boot_id": self._boot_id,
            "uptime_s": self.uptime_s(),
        }

    def __repr__(self) -> str:
        return "Clock(name=%r, boot_id=%r)" % (self._name, self._boot_id)


class ManualClock(Clock):
    DEFAULT_UTC_NS = 1767225600000000000  # 2026-01-01T00:00:00Z

    def __init__(
        self,
        mono_ns: int = 0,
        utc_ns: Optional[int] = None,
        boot_id: str = "manual-boot",
        name: str = "manual",
    ) -> None:
        self._mono = int(mono_ns)
        self._utc = int(utc_ns) if utc_ns is not None else self.DEFAULT_UTC_NS
        super(ManualClock, self).__init__(
            mono_source=lambda: self._mono,
            utc_source=lambda: self._utc,
            boot_id=boot_id,
            name=name,
        )

    def advance(self, seconds: float) -> "ManualClock":
        delta = int(float(seconds) * NS_PER_S)
        if delta < 0:
            raise ValueError("a monotonic clock cannot move backwards")
        self._mono += delta
        self._utc += delta
        return self

    def step_wall_clock(self, seconds: float) -> "ManualClock":
        self._utc += int(float(seconds) * NS_PER_S)
        return self
