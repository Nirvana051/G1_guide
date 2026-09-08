"""Voice service: TTS, volume, LED."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from g1_api.core.safety import CommandClass
from g1_api.services.base import BaseService


class VoiceService(BaseService):
    async def tts(
        self,
        text: str,
        speaker_id: Optional[int] = None,
        wait: bool = False,
        caller: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``speaker_id`` None falls back to ``voice.default_speaker_id``
        (0=中文, 1=English on the G1)."""
        await self.guard(CommandClass.VOICE, "tts", caller=caller)
        if speaker_id is None:
            speaker_id = int(self._core.config.voice.default_speaker_id)
        est = await self._core.adapter.voice.speak(text, speaker_id)
        if wait:
            await asyncio.sleep(est)
        return {"accepted": True, "est_duration_s": est}

    async def get_volume(self) -> int:
        return await self._core.adapter.voice.get_volume()

    async def set_volume(self, volume: int, caller: Optional[str] = None) -> int:
        await self.guard(CommandClass.VOICE, "set volume", caller=caller)
        return await self._core.adapter.voice.set_volume(volume)

    _led_hold_task: Optional[Any] = None

    async def set_led(
        self, r: int, g: int, b: int, hold_s: float = 0.0, caller: Optional[str] = None
    ) -> None:
        """Set the head LED. ``hold_s > 0`` keeps re-sending the color once a
        second for that long -- the robot's own status indication overwrites
        custom colors, so holding a color means refreshing it. A new request
        (with or without hold) cancels the previous hold."""
        await self.guard(CommandClass.VOICE, "set led", caller=caller)
        if self._led_hold_task is not None and not self._led_hold_task.done():
            self._led_hold_task.cancel()
            self._led_hold_task = None
        await self._core.adapter.voice.set_led(r, g, b)
        hold = float(hold_s or 0.0)
        if hold > 0:
            async def _refresh() -> None:
                loop = asyncio.get_event_loop()
                deadline = loop.time() + hold
                try:
                    while loop.time() < deadline:
                        await asyncio.sleep(1.0)
                        await self._core.adapter.voice.set_led(r, g, b)
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    return  # a failed refresh must never crash the loop

            self._led_hold_task = asyncio.ensure_future(_refresh())
