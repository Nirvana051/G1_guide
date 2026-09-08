"""Transport for user-defined external action executors.

Lives in ``utils`` deliberately: the pure-logic layers (``tour`` included) are
forbidden from importing network clients by the layering guard; the tour
executor calls this helper instead of urllib directly.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def post_json(url: str, payload: Dict[str, Any], timeout_s: float = 15.0) -> int:
    """POST ``payload`` as JSON to ``url``; returns the HTTP status code.

    Blocking -- run it in an executor thread from async code.
    """
    from urllib.request import Request, urlopen

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout_s) as response:
        return int(getattr(response, "status", 200))
