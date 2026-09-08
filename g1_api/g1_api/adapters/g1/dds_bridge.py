"""DDS bridge for the real G1: LocoClient / AudioClient / ArmClient.

All ``unitree_sdk2py`` imports live inside methods. The DDS initialization order
is the hard constraint from appendix A: ``ChannelFactoryInitialize(0, iface)``
once per process, then ``LocoClient.Init()`` **before** ``AudioClient`` (TTS
silently fails otherwise). ``iface`` is read from ``G1_NETWORK_INTERFACE`` with
**no default** -- the course code ships three different defaults at once, which
is a known accident source.
"""

from __future__ import annotations

import threading
from typing import Any, List, Optional

__all__ = ["DdsBridge", "StubDdsBridge"]

_DDS_INIT_LOCK = threading.Lock()


class DdsBridge(object):
    def __init__(self, iface: Optional[str], domain_id: int = 0, timeout: float = 10.0) -> None:
        if not iface:
            raise RuntimeError(
                "G1_NETWORK_INTERFACE (mode.network_interface) is required for the "
                "real adapter; refusing to guess between eth0/eno1/enp4s0."
            )
        self._iface = str(iface)
        self._domain_id = int(domain_id)
        self._timeout = float(timeout)
        self._loco = None
        self._audio = None
        self._arm = None
        self._initialized = False

    def _ensure_factory(self) -> None:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize

        with _DDS_INIT_LOCK:
            ChannelFactoryInitialize(self._domain_id, self._iface)

    def _init_loco(self) -> Any:
        from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient

        loco = LocoClient()
        loco.SetTimeout(self._timeout)
        loco.Init()
        return loco

    def _init_audio(self) -> Any:
        from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

        audio = AudioClient()
        audio.SetTimeout(self._timeout)
        audio.Init()
        return audio

    def _init_arm(self) -> Any:
        from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient

        arm = G1ArmActionClient()
        arm.Init()
        return arm

    def initialize(self) -> bool:
        if self._initialized:
            return True
        self._ensure_factory()
        # LocoClient must initialize BEFORE AudioClient (appendix A.6).
        self._loco = self._init_loco()
        self._audio = self._init_audio()
        self._arm = self._init_arm()
        self._initialized = True
        return True

    @property
    def loco(self) -> Any:
        return self._loco

    @property
    def audio(self) -> Any:
        return self._audio

    @property
    def arm(self) -> Any:
        return self._arm

    def close(self) -> None:
        self._loco = None
        self._audio = None
        self._arm = None
        self._initialized = False


class _StubLoco(object):
    """Log-only LocoClient stand-in (simulation: /cmd_vel drives the base)."""

    def __init__(self, log: List[str]) -> None:
        self._log = log

    def Move(self, vx: float, vy: float, vyaw: float, continous_move: bool = False) -> None:
        self._log.append("loco.Move(%.3f, %.3f, %.3f)" % (vx, vy, vyaw))

    def StopMove(self) -> None:
        self._log.append("loco.StopMove()")


class _StubAudio(object):
    """Log-only AudioClient stand-in with mock volume state."""

    def __init__(self, log: List[str]) -> None:
        self._log = log
        self.volume = 100
        self.led = (0, 255, 0)

    def TtsMaker(self, text: str, speaker_id: int = 0) -> int:
        self._log.append("audio.TtsMaker(%r, %d)" % (text, speaker_id))
        return 0

    def GetVolume(self):
        return 0, {"volume": self.volume}

    def SetVolume(self, volume: int) -> int:
        self.volume = max(0, min(100, int(volume)))
        return 0

    def LedControl(self, r: int, g: int, b: int) -> int:
        self.led = (int(r), int(g), int(b))
        return 0


class _StubArm(object):
    """Log-only G1ArmActionClient stand-in."""

    def __init__(self, log: List[str]) -> None:
        self._log = log

    def ExecuteAction(self, action_id: int) -> int:
        self._log.append("arm.ExecuteAction(%d)" % int(action_id))
        return 0

    def GetActionList(self):
        return None  # adapter falls back to the appendix A.7 table


class StubDdsBridge(object):
    """Drop-in DdsBridge replacement for simulation (mode.dds_backend=stub).

    Same surface as :class:`DdsBridge`; every call is logged to :attr:`log`
    instead of reaching DDS. Needs no network interface.
    """

    def __init__(self, iface: Optional[str] = None, domain_id: int = 0, timeout: float = 10.0) -> None:
        self.log: List[str] = []
        self._loco = _StubLoco(self.log)
        self._audio = _StubAudio(self.log)
        self._arm = _StubArm(self.log)
        self._initialized = False

    def initialize(self) -> bool:
        self._initialized = True
        return True

    @property
    def loco(self) -> Any:
        return self._loco

    @property
    def audio(self) -> Any:
        return self._audio

    @property
    def arm(self) -> Any:
        return self._arm

    def close(self) -> None:
        self._initialized = False
