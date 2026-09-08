"""External (replay) action library service: upload / list / delete /
execute-by-id, mirroring the official arm-action usage pattern.

Execution POSTs a job to the user's replay executor (``g1_replay_min``'s
action server at ``tour.external_executor_url``) and holds the
``external_motion_running`` interlock while the replay runs, so official arm
actions and navigation cannot fight the replay over the robot.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from g1_api.core.safety import CommandClass
from g1_api.errors import AdapterError, ConflictError, NotFoundError, ValidationError
from g1_api.models.external_action import (
    ARM_DOF,
    ExternalActionMeta,
    build_execute_payload,
    library_list,
    library_load,
    library_paths,
    parse_npy_header,
    sanitize_action_id,
)
from g1_api.services.base import BaseService

__all__ = ["ExternalActionService"]


class ExternalActionService(BaseService):
    @property
    def _dir(self) -> str:
        return os.path.expanduser(self._core.config.tour.external_actions_dir)

    # ------------------------------------------------------------- library --

    async def list_actions(self) -> List[Dict[str, Any]]:
        return [meta.to_dict() for meta in library_list(self._dir)]

    async def get_meta(self, action_id: str) -> Dict[str, Any]:
        meta = library_load(self._dir, action_id)
        if meta is None:
            raise NotFoundError(resource="external action", identifier=action_id, source="services.external_actions")
        return meta.to_dict()

    async def get_file(self, action_id: str) -> bytes:
        meta = library_load(self._dir, action_id)
        if meta is None:
            raise NotFoundError(resource="external action", identifier=action_id, source="services.external_actions")
        npy_path, _ = library_paths(self._dir, action_id)
        with open(npy_path, "rb") as handle:
            return handle.read()

    async def upload(
        self,
        name: str,
        data: bytes,
        frequency_hz: float = 30.0,
        velocity_limit: float = 20.0,
        description: str = "",
    ) -> Dict[str, Any]:
        try:
            action_id = sanitize_action_id(name)
        except ValueError as exc:
            raise ValidationError(str(exc), field="name", source="services.external_actions")
        try:
            shape, dtype, fortran = parse_npy_header(data)
        except ValueError as exc:
            raise ValidationError("not a valid .npy: %s" % exc, field="body", source="services.external_actions")
        if len(shape) != 2 or shape[1] < ARM_DOF:
            raise ValidationError(
                "action shape %r invalid: need (N, >=%d) dual-arm trajectory" % (shape, ARM_DOF),
                field="body", source="services.external_actions")
        if fortran:
            raise ValidationError("fortran-ordered .npy is not supported", field="body", source="services.external_actions")
        if float(frequency_hz) <= 0:
            raise ValidationError("frequency_hz must be > 0", field="frequency_hz", source="services.external_actions")
        meta = ExternalActionMeta(
            action_id=action_id,
            name=str(name),
            frames=int(shape[0]),
            dims=int(shape[1]),
            frequency_hz=float(frequency_hz),
            velocity_limit=float(velocity_limit),
            size_bytes=len(data),
            created_unix=time.time(),
            description=str(description),
        )
        os.makedirs(self._dir, exist_ok=True)
        npy_path, meta_path = library_paths(self._dir, action_id)
        with open(npy_path, "wb") as handle:
            handle.write(data)
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(meta.to_dict(), handle, ensure_ascii=False, indent=2)
        return meta.to_dict()

    async def delete(self, action_id: str) -> None:
        meta = library_load(self._dir, action_id)
        if meta is None:
            raise NotFoundError(resource="external action", identifier=action_id, source="services.external_actions")
        for path in library_paths(self._dir, action_id):
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------- execute --

    async def execute(self, action_id: str, caller: Optional[str] = None) -> Dict[str, Any]:
        meta = library_load(self._dir, action_id)
        if meta is None:
            raise NotFoundError(resource="external action", identifier=action_id, source="services.external_actions")
        url = self._core.config.tour.external_executor_url
        if not url:
            raise ConflictError(
                "tour.external_executor_url is not configured; start the replay "
                "action server and set G1_API_TOUR_EXTERNAL_EXECUTOR_URL",
                source="services.external_actions")
        # The replay drives the arms: same capability flag as official arm
        # actions, and the external_motion_running interlock below keeps
        # official arm actions / navigation out while it runs.
        await self.guard(CommandClass.ARM, "execute external action %r" % action_id, caller=caller)

        from g1_api.utils.external_executor import post_json

        payload = {"action": build_execute_payload(meta, self._dir)}
        timeout = meta.duration_s + float(
            self._core.config.tour.external_execute_timeout_margin_s)

        def _post() -> int:
            return post_json(url, payload, timeout_s=timeout)

        self._core.set_external_motion_running(True)
        started = time.monotonic()
        try:
            code = await asyncio.get_event_loop().run_in_executor(None, _post)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(
                "external executor failed: %s" % exc,
                adapter="external", operation="execute", source="services.external_actions")
        finally:
            self._core.set_external_motion_running(False)
        if int(code) >= 300:
            raise ConflictError(
                "external executor refused action %r (HTTP %d)" % (action_id, code),
                source="services.external_actions")
        return {
            "action_id": action_id,
            "status": "finished",
            "elapsed_s": round(time.monotonic() - started, 2),
            "executor_http_status": int(code),
        }
