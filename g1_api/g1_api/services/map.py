"""Map service: the ``.g1map`` package lifecycle and map selection."""

from __future__ import annotations

from typing import Dict, List, Optional

from g1_api.core.safety import CommandClass
from g1_api.errors import SafetyInterlockError
from g1_api.models.map import (
    MapManifest,
    MapPackage,
    MappingStatus,
    TourPointModel,
    unpack_map_package,
)
from g1_api.services.base import BaseService


def _pgm_dims(data: bytes):
    """(width, height) of a binary P5 PGM, or None if it is not one."""
    if not data.startswith(b"P5"):
        return None
    tokens: List[bytes] = []
    pos = 2
    while len(tokens) < 3 and pos < len(data):
        end = data.find(b"\n", pos)
        if end < 0:
            return None
        line = data[pos:end]
        if not line.strip().startswith(b"#"):
            tokens.extend(line.split())
        pos = end + 1
    if len(tokens) < 2:
        return None
    try:
        return (int(tokens[0]), int(tokens[1]))
    except ValueError:
        return None


class MapService(BaseService):
    async def list_maps(self) -> List[MapManifest]:
        return await self._core.adapter.map.list_maps()

    async def upload_map(self, data: bytes, caller: Optional[str] = None) -> MapManifest:
        await self.guard(CommandClass.MAP_WRITE, "upload map package", caller=caller)
        package = unpack_map_package(data)
        return await self._core.adapter.map.upload_map(package)

    async def download_map(self, map_id: str) -> MapPackage:
        return await self._core.adapter.map.download_map(map_id)

    async def current_map(self) -> Optional[MapManifest]:
        return await self._core.adapter.map.get_current_map()

    async def select_map(self, map_id: str, caller: Optional[str] = None) -> MapManifest:
        await self.guard(CommandClass.MAP_WRITE, "select map %s" % map_id, caller=caller)
        return await self._core.adapter.map.select_map(map_id)

    async def delete_map(self, map_id: str, caller: Optional[str] = None) -> bool:
        await self.guard(CommandClass.MAP_WRITE, "delete map %s" % map_id, caller=caller)
        return await self._core.adapter.map.delete_map(map_id)

    async def set_tour_points(
        self, map_id: str, tour_points: List[TourPointModel], caller: Optional[str] = None
    ) -> MapManifest:
        await self.guard(CommandClass.MAP_WRITE, "update tour points", caller=caller)
        return await self._core.adapter.map.set_tour_points(map_id, tour_points)

    # ------------------------------------------------------------ map editor

    async def get_grid(self, map_id: str) -> bytes:
        """The 2D grid (grid.pgm bytes) of a library map."""
        package = await self._core.adapter.map.download_map(map_id)
        try:
            return package.files["grid.pgm"]
        except KeyError:
            from g1_api.errors import NotFoundError

            raise NotFoundError(resource="grid.pgm", identifier=map_id, source="services.map")

    async def update_grid(self, map_id: str, pgm: bytes, caller: Optional[str] = None) -> MapManifest:
        """Overwrite a library map's 2D grid in place (same map_id).

        The PGM must keep the original dimensions -- the yaml origin/resolution
        describe that exact raster, and a resize would silently shift the world.
        """
        from g1_api.errors import ValidationError
        from g1_api.models.map import sha256_hex
        import dataclasses

        await self.guard(CommandClass.MAP_WRITE, "edit grid of map %s" % map_id, caller=caller)
        package = await self._core.adapter.map.download_map(map_id)
        old_dims = _pgm_dims(package.files.get("grid.pgm", b""))
        new_dims = _pgm_dims(pgm)
        if new_dims is None:
            raise ValidationError("body is not a binary P5 PGM", field="body", source="services.map")
        if old_dims is not None and new_dims != old_dims:
            raise ValidationError(
                "grid dimensions must stay %sx%s (got %sx%s)"
                % (old_dims[0], old_dims[1], new_dims[0], new_dims[1]),
                field="body", source="services.map",
            )
        files = dict(package.files)
        files["grid.pgm"] = pgm
        checksums = dict(package.manifest.checksums)
        if checksums:
            checksums["grid.pgm"] = sha256_hex(pgm)
        manifest = dataclasses.replace(package.manifest, checksums=checksums)
        return await self._core.adapter.map.upload_map(MapPackage(manifest=manifest, files=files))

    # ------------------------------------------------------------ mapping

    async def start_mapping(self, caller: Optional[str] = None) -> MappingStatus:
        await self.guard(CommandClass.MAP_WRITE, "start mapping session", caller=caller)
        return await self._core.adapter.map.start_mapping()

    async def mapping_status(self) -> MappingStatus:
        return await self._core.adapter.map.mapping_status()

    async def finish_mapping(
        self, map_id: str, name: str = "", caller: Optional[str] = None
    ) -> MapManifest:
        await self.guard(CommandClass.MAP_WRITE, "finish mapping session", caller=caller)
        return await self._core.adapter.map.finish_mapping(map_id, name)

    async def cancel_mapping(self, caller: Optional[str] = None) -> bool:
        await self.guard(CommandClass.MAP_WRITE, "cancel mapping session", caller=caller)
        return await self._core.adapter.map.cancel_mapping()
