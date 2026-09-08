#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""g1_api 外部调用示例：一个极简客户端 + 常用场景串讲。

依赖仅 requests：  pip install requests
运行前提：g1_api 已启动（真机 http://192.168.220.8:1448，仿真 http://127.0.0.1:1448）

    python3 g1_client_demo.py http://192.168.220.8:1448

《G1 系统全书》(docs/system_bible.html) 第 11 节逐段解释了本文件。
"""
import sys
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:1448"


class G1Client:
    """薄封装：只做三件事——拼 URL、抛出 HTTP 错误、动作轮询。"""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.s = requests.Session()

    # ---- 基础 ----
    def get(self, path, **kw):
        r = self.s.get(self.base + path, timeout=kw.pop("timeout", 10), **kw)
        return r

    def post(self, path, json=None, **kw):
        r = self.s.post(self.base + path, json=json, timeout=kw.pop("timeout", 180), **kw)
        return r

    # ---- 动作（思岚异步模型：提交→轮询→终态）----
    def submit_action(self, action_name, options):
        r = self.post("/api/core/motion/v1/actions",
                      json={"action_name": action_name, "options": options})
        r.raise_for_status()
        return r.json()["action_id"]

    def wait_action(self, action_id, timeout_s=180.0, poll_s=0.5):
        """轮询到终态。返回终态 dict；status=4 是终态，result 0/-1/-2=成功/失败/取消。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            r = self.get("/api/core/motion/v1/actions/%s" % action_id)
            if r.status_code == 404:      # 槽位已被新动作顶替等罕见情况
                raise RuntimeError("action %s vanished" % action_id)
            state = r.json()["state"]
            if state["status"] == 4:
                return r.json()
            time.sleep(poll_s)
        # 超时：取消当前动作（单槽语义：:current 即它）
        self.s.delete(self.base + "/api/core/motion/v1/actions/:current", timeout=10)
        raise TimeoutError("action %s not terminal in %.0fs" % (action_id, timeout_s))

    def move_to(self, x, y, yaw=None, reach=0.35, yaw_tol=0.5, timeout_s=180.0):
        opts = {"target": {"x": x, "y": y}, "with_yaw": yaw is not None,
                "reach_threshold": reach, "yaw_threshold": yaw_tol}
        if yaw is not None:
            opts["yaw"] = yaw
        aid = self.submit_action("slamtec.agent.actions.MoveToAction", opts)
        return self.wait_action(aid, timeout_s=timeout_s)


def main():
    c = G1Client(BASE)

    # ── 例1 健康检查：互锁清单是排障第一入口 ────────────────────────────
    health = c.get("/api/core/system/v1/robot/health").json()
    print("[1] 互锁:", health.get("interlocks"))

    # ── 例2 地图库 → 选图 → 重定位（服务重启后必做的两步）────────────────
    maps = c.get("/api/core/slam/v1/maps").json()["maps"]
    print("[2] 地图库:", [m["map_id"] for m in maps])
    if not maps:
        sys.exit("地图库为空：先建图（/mapping/:start）或上传 .g1map")
    map_id = maps[0]["map_id"]
    c.post("/api/core/slam/v1/maps/%s/:select" % map_id, timeout=150).raise_for_status()
    # 机器人须物理摆在地标位（通常=建图起点），朝向对齐后：
    c.post("/api/core/slam/v1/localization/:relocalize",
           json={"x": 0, "y": 0, "yaw": 0}, timeout=60).raise_for_status()
    pose = c.get("/api/core/slam/v1/localization/pose").json()
    print("[2] 定位:", pose)

    # ── 例3 导航到点（异步动作全流程）────────────────────────────────────
    final = c.move_to(0.5, 0.0, yaw=0.0)
    print("[3] MoveTo:", final["state"])   # reason 里能看出分层到点的路径

    # ── 例4 外部动作：上传 .npy → 按 id 执行（同步，返回=回放结束）────────
    # with open("wave.npy", "rb") as f:
    #     c.s.post(BASE + "/api/core/motion/v1/external-actions",
    #              params={"name": "wave", "frequency": 30, "velocity_limit": 10},
    #              data=f.read(),
    #              headers={"Content-Type": "application/octet-stream"}).raise_for_status()
    acts = c.get("/api/core/motion/v1/external-actions").json()["actions"]
    print("[4] 外部动作库:", [a["action_id"] for a in acts])
    # if acts:
    #     r = c.post("/api/core/motion/v1/external-actions/%s/:execute" % acts[0]["action_id"])
    #     print("[4] 执行:", r.json())

    # ── 例5 写航迹点 + 启动导览 + 用事件流观察 ──────────────────────────
    c.s.put(BASE + "/api/core/slam/v1/maps/%s/tour-points" % map_id, json={"points": [
        {"name": "P1", "x": 0.5, "y": 0.0, "yaw": 0.0,
         "actions": [{"executor": "onboard", "type": "arm", "id": 26}],
         "tts_text": "Hello, welcome to our showroom.", "dwell_s": 1.0},
    ]}, timeout=15).raise_for_status()
    c.post("/api/tour/v1/tours/:start").raise_for_status()
    cursor = None
    while True:
        params = {"from_cursor": cursor} if cursor else {}
        events = c.get("/api/tour/v1/events", params=params).json()["events"]
        for e in events:
            cursor = e["cursor"]
            print("[5] 事件:", e["type"], str(e.get("payload", ""))[:80])
        status = c.get("/api/tour/v1/tours/:current").json()["status"]
        if status in ("finished", "failed", "stopped"):
            print("[5] 导览终态:", status)
            break
        time.sleep(1)

    # ── 例6 声与光 ───────────────────────────────────────────────────────
    c.post("/api/core/voice/v1/tts", json={"text": "Demo finished."})  # 默认英文
    c.s.put(BASE + "/api/core/voice/v1/led",
            json={"r": 0, "g": 255, "b": 0, "hold_s": 5}, timeout=10)


if __name__ == "__main__":
    main()
