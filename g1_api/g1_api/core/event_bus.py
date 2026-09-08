"""A small thread-safe event ring.

The tour executor publishes ``point_reached`` / ``tts_started`` / ``action_done``
/ ``point_failed`` events here; the SDK console's tour panel polls them. Events
carry a monotonic cursor so a poller can resume without a gap.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from g1_api.models.geometry import Stamp
from g1_api.utils.ids import new_cursor

__all__ = ["Event", "EventBus", "DEFAULT_RING_CAPACITY"]

DEFAULT_RING_CAPACITY = 4096


class Event(object):
    __slots__ = ("cursor", "stamp", "topic", "type", "payload", "source")

    def __init__(
        self,
        cursor: str,
        stamp: Stamp,
        topic: str,
        type: str,  # noqa: A002 - wire field name
        payload: Dict[str, Any],
        source: Optional[str],
    ) -> None:
        self.cursor = cursor
        self.stamp = stamp
        self.topic = topic
        self.type = type
        self.payload = payload
        self.source = source

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "cursor": self.cursor,
            "topic": self.topic,
            "type": self.type,
            "payload": dict(self.payload),
        }
        out.update(self.stamp.to_dict())
        if self.source:
            out["source"] = self.source
        return out


class EventBus(object):
    def __init__(self, capacity: int = DEFAULT_RING_CAPACITY, clock: Optional[Any] = None) -> None:
        if int(capacity) < 1:
            raise ValueError("event ring capacity must be >= 1")
        self._capacity = int(capacity)
        self._clock = clock
        self._buffer: List[Event] = []
        self._next_index = 1
        self._dropped = 0
        self._lock = threading.RLock()

    def publish(
        self,
        topic: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        stamp: Optional[Stamp] = None,
    ) -> Event:
        resolved_stamp = stamp
        if resolved_stamp is None:
            resolved_stamp = self._clock.stamp() if self._clock is not None else Stamp.now()
        with self._lock:
            index = self._next_index
            self._next_index += 1
            event = Event(
                cursor=new_cursor(index),
                stamp=resolved_stamp,
                topic=str(topic),
                type=str(event_type),
                payload=dict(payload or {}),
                source=source,
            )
            self._buffer.append(event)
            if len(self._buffer) > self._capacity:
                overflow = len(self._buffer) - self._capacity
                del self._buffer[:overflow]
                self._dropped += overflow
            return event

    def history(self, from_cursor: Optional[str] = None, limit: Optional[int] = None) -> List[Event]:
        start = 0
        if from_cursor is not None and from_cursor != "":
            digits = from_cursor[4:] if from_cursor.startswith("evt_") else from_cursor
            if digits.isdigit():
                start = int(digits)
        out: List[Event] = []
        with self._lock:
            for event in self._buffer:
                index = int(event.cursor[4:])
                if index > start:
                    out.append(event)
                    if limit is not None and len(out) >= limit:
                        break
        return out

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "capacity": self._capacity,
                "buffered": len(self._buffer),
                "published": self._next_index - 1,
                "dropped": self._dropped,
            }

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
