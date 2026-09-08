"""Identifier generation.

Four id spaces: UUIDs for canonical resource ids, a monotonic integer for the
Slamtec-compatible ``action_id``, short request ids for correlation, and
``evt_`` cursors for the event stream.
"""

from __future__ import annotations

import itertools
import threading
import uuid
from typing import Iterator, Optional

__all__ = [
    "new_uuid",
    "new_action_id",
    "reset_action_ids",
    "new_request_id",
    "new_cursor",
    "parse_cursor",
    "CURSOR_PREFIX",
    "REQUEST_ID_PREFIX",
]

CURSOR_PREFIX = "evt_"
REQUEST_ID_PREFIX = "req_"

_action_lock = threading.Lock()
_action_counter: Iterator[int] = itertools.count(1)


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_action_id() -> int:
    """Return the next Slamtec-compatible integer ``action_id`` (monotonic per boot)."""
    with _action_lock:
        return next(_action_counter)


def reset_action_ids(start: int = 1) -> None:
    global _action_counter
    if start < 1:
        raise ValueError("action ids start at 1")
    with _action_lock:
        _action_counter = itertools.count(start)


def new_request_id() -> str:
    return REQUEST_ID_PREFIX + uuid.uuid4().hex[:12]


def new_cursor(n: int) -> str:
    if n < 0:
        raise ValueError("cursor index must be >= 0")
    return "%s%08d" % (CURSOR_PREFIX, n)


def parse_cursor(cursor: Optional[str]) -> Optional[int]:
    if cursor is None or cursor == "":
        return None
    text = str(cursor)
    if not text.startswith(CURSOR_PREFIX):
        raise ValueError("cursor %r must start with %r" % (cursor, CURSOR_PREFIX))
    digits = text[len(CURSOR_PREFIX):]
    if not digits.isdigit():
        raise ValueError("cursor %r has a non-numeric index" % (cursor,))
    return int(digits, 10)
