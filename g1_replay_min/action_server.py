#!/usr/bin/env python3
"""外部动作执行器 —— 把 replay_min 包成一个小 HTTP 服务，供 g1_api 调用。

契约（g1_api 的外部动作库 / 导览外部动作都发这个）：
    POST /execute
        {"action": {"type": "replay", "file": "/abs/path.npy",
                    "frequency": 30.0, "velocity_limit": 20.0, ...},
         "point": {...可选，导览点上下文...}}
    同步执行：HTTP 响应返回 = 回放结束。回放中再来请求 → 409（单飞）。
    GET /health  → {"status": "ok", "busy": false}

安全/互斥：
    - 单飞锁：同一时刻只跑一条回放
    - g1_api 侧在执行期挂 external_motion_running 互锁（挡官方手臂动作与导航）
    - replay_min --motion 走 rt/arm_sdk：腿仍归运控，结束权重缓降交还，天然不打架

环境变量：
    G1_EXTEXEC_PORT      监听端口（默认 9100，绑 127.0.0.1）
    G1_EXTEXEC_DRY_RUN   =1 时不驱动真机，按时长 sleep（联调/仿真用）
    UNITREE_NIC          DDS 网卡（真机上 eth0），透传给 replay_min

用法：
    python3 action_server.py            # 真机（先确认机器人站立、急停在手）
    G1_EXTEXEC_DRY_RUN=1 python3 action_server.py   # 联调
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("G1_EXTEXEC_PORT", "9100"))
DRY_RUN = os.environ.get("G1_EXTEXEC_DRY_RUN", "") in ("1", "true", "yes")

_busy_lock = threading.Lock()


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run_replay(action):
    """跑一条回放或一组连播（replay_sequence）；返回 (ok, detail)。"""
    if action.get("type") == "replay_sequence":
        items = list(action.get("items") or [])
        if not items:
            return False, "empty sequence"
        for it in items:
            if not os.path.isfile(it.get("file", "")):
                return False, "file not found: %r" % it.get("file")
        duration = float(action.get("duration_s", 0.0)) or sum(
            float(i.get("duration_s", 0.0)) for i in items)
        if DRY_RUN:
            log("DRY-RUN 连播 x%d（%.1fs）" % (len(items), duration))
            time.sleep(min(duration, 5.0))
            return True, "dry-run sequence x%d finished" % len(items)
        seq = [{"file": i["file"], "frequency": i.get("frequency", 30.0),
                "velocity_limit": i.get("velocity_limit", 20.0)} for i in items]
        cmd = [
            sys.executable, os.path.join(HERE, "replay_min.py"),
            "--sequence-json", json.dumps(seq),
            "--motion", "--yes",
        ]
        log("连播执行: x%d 条轨迹" % len(items))
        timeout = duration + 60.0 + 10.0 * len(items)
        return _run_cmd(cmd, timeout)

    path = action.get("file", "")
    if not path or not os.path.isfile(path):
        return False, "file not found: %r" % path
    frequency = float(action.get("frequency", 30.0))
    velocity_limit = float(action.get("velocity_limit", 20.0))
    duration = float(action.get("duration_s", 0.0))

    if DRY_RUN:
        log("DRY-RUN 回放 %s（%.1fs）" % (os.path.basename(path), duration))
        time.sleep(min(duration, 5.0))
        return True, "dry-run finished"

    cmd = [
        sys.executable, os.path.join(HERE, "replay_min.py"),
        "--actions", path,
        "--frequency", str(frequency),
        "--velocity-limit", str(velocity_limit),
        "--motion",       # rt/arm_sdk：与运控共存（机器人须站立）
        "--yes",          # 跳过交互确认（调用方已过安全门）
    ]
    log("执行: %s" % " ".join(cmd))
    timeout = duration + 60.0  # 起始位姿移动 + 回放 + 权重交还的富余
    return _run_cmd(cmd, timeout)


def _run_cmd(cmd, timeout):
    try:
        proc = subprocess.run(cmd, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        return False, "replay timed out after %.0fs" % timeout
    tail = proc.stdout.decode("utf-8", "replace").strip().splitlines()[-5:]
    if proc.returncode != 0:
        return False, "replay exit %d: %s" % (proc.returncode, " | ".join(tail))
    return True, " | ".join(tail[-2:])



class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "busy": _busy_lock.locked(),
                             "dry_run": DRY_RUN})
        else:
            self._send(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path != "/execute":
            self._send(404, {"error": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "bad json: %s" % exc})
            return
        action = payload.get("action", payload)
        if action.get("type") not in ("replay", "replay_sequence"):
            self._send(400, {"error": "unsupported action type %r" % action.get("type")})
            return
        if not _busy_lock.acquire(blocking=False):
            self._send(409, {"error": "busy: a replay is already running"})
            return
        try:
            ok, detail = run_replay(action)
        finally:
            _busy_lock.release()
        if ok:
            log("完成: %s" % detail)
            self._send(200, {"status": "finished", "detail": detail})
        else:
            log("失败: %s" % detail)
            self._send(500, {"status": "failed", "detail": detail})

    def log_message(self, fmt, *args):  # 静默默认访问日志（自己打关键日志）
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log("外部动作执行器就绪: http://127.0.0.1:%d  (dry_run=%s, NIC=%s)"
        % (PORT, DRY_RUN, os.environ.get("UNITREE_NIC", "autodetermine")))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("退出")


if __name__ == "__main__":
    main()
