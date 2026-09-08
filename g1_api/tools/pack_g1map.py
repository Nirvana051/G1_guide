#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把建图产物打包成 .g1map 地图包。

真机建图后 ``navigate/map/`` 下有四件套：``map.pcd``（FAST-LIO 重定位参考云）、
``ground_map.pcd``、``mymap.pgm`` + ``mymap.yaml``（map_saver 的 2D 栅格）。
本工具把它们打成 g1_api 的 ``.g1map``（tar.gz + manifest + sha256）：

    python3 tools/pack_g1map.py \
        --src /home/unitree/abotclaw_nv/navigate/map \
        --map-id showroom-1f --name "一楼展厅" \
        --out showroom-1f.g1map

自动处理两个已知陷阱：yaml 的 ``image`` 字段改写为相对名 ``grid.pgm``；
``nan``/``-nan`` 替换为 ``0.0``（yaml-cpp 连 ``-0`` 整数都会拒绝）。

导览点可选：``--tour-points points.json``，内容形如
``[{"name":"大厅","x":1.0,"y":2.0,"yaw":0.0,"tts_text":"欢迎",
    "actions":[{"type":"arm","id":26}],"dwell_s":0.5}]``；
不提供也可以之后通过 API（PUT /api/tour/v1/tours）回写。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from g1_api.models.map import MapManifest, TourPointModel, pack_map_package  # noqa: E402


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


def _find(src, names):
    for name in names:
        path = os.path.join(src, name)
        if os.path.isfile(path):
            return path
    raise SystemExit("在 %s 下找不到 %s 中的任何一个" % (src, " / ".join(names)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, help="建图产物目录（含四件套）")
    ap.add_argument("--map-id", required=True, help="地图 id（唯一，URL 安全）")
    ap.add_argument("--name", default="", help="人类可读名（默认同 map-id）")
    ap.add_argument("--out", default="", help="输出文件（默认 <map-id>.g1map）")
    ap.add_argument("--tour-points", default="", help="导览点 JSON 文件（可选）")
    args = ap.parse_args()

    files = {
        "map.pcd": _read(_find(args.src, ["map.pcd"])),
        "ground_map.pcd": _read(_find(args.src, ["ground_map.pcd"])),
        "grid.pgm": _read(_find(args.src, ["mymap.pgm", "grid.pgm"])),
    }
    yaml_text = _read(_find(args.src, ["mymap.yaml", "grid.yaml"])).decode("utf-8")
    yaml_text = re.sub(r"image:\s*\S+", "image: grid.pgm", yaml_text)
    yaml_text = re.sub(r"-?\bnan\b", "0.0", yaml_text)
    files["grid.yaml"] = yaml_text.encode("utf-8")

    resolution, origin = 0.05, (0.0, 0.0, 0.0)
    match = re.search(r"resolution:\s*([0-9.]+)", yaml_text)
    if match:
        resolution = float(match.group(1))
    match = re.search(r"origin:\s*\[([^\]]+)\]", yaml_text)
    if match:
        parts = [float(p) for p in match.group(1).split(",")]
        if len(parts) >= 3:
            origin = tuple(parts[:3])

    points = ()
    if args.tour_points:
        with open(args.tour_points, "r", encoding="utf-8") as handle:
            points = tuple(TourPointModel.from_dict(p) for p in json.load(handle))
    else:
        # 编辑已解包的 .g1map 再重打包时，继承原 manifest 里的导览点。
        old_manifest = os.path.join(args.src, "manifest.json")
        if os.path.isfile(old_manifest):
            with open(old_manifest, "r", encoding="utf-8") as handle:
                old = MapManifest.from_dict(json.load(handle))
            points = old.tour_points
            if points:
                print("从 %s 继承 %d 个导览点" % (old_manifest, len(points)))

    manifest = MapManifest(
        map_id=args.map_id,
        name=args.name or args.map_id,
        created_utc=time.time(),
        resolution=resolution,
        origin=origin,
        tour_points=points,
    )
    out = args.out or ("%s.g1map" % args.map_id)
    blob = pack_map_package(manifest, files)
    with open(out, "wb") as handle:
        handle.write(blob)
    print("已写出 %s（%d 字节, map_id=%s, resolution=%s, origin=%s, 导览点 %d 个）"
          % (out, len(blob), args.map_id, resolution, origin, len(points)))
    print("上传: curl -X POST http://<robot>:1448/api/core/slam/v1/maps "
          "-H 'Content-Type: application/octet-stream' --data-binary @%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
