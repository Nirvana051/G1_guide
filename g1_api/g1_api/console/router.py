"""Routes for the SDK debug console.

Serves the accordion page, its static assets, and a ``meta.json`` that carries
the endpoint catalog (Chinese description, parameter table, pre-filled sample,
safety badge) the JavaScript renders. All ``include_in_schema=False``.
"""

from __future__ import annotations

import mimetypes
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from g1_api.config import AppConfig

__all__ = ["CONSOLE_PREFIX", "STATIC_DIR", "router", "install_console"]

CONSOLE_PREFIX = "/sdk"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

router = APIRouter()


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _safe_asset_path(asset_path: str) -> Optional[str]:
    cleaned = str(asset_path or "").strip().lstrip("/")
    if not cleaned or "\x00" in cleaned:
        return None
    root = os.path.realpath(STATIC_DIR)
    candidate = os.path.realpath(os.path.join(root, cleaned))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    if os.path.splitext(candidate)[1].lower() not in _CONTENT_TYPES:
        return None
    return candidate


def _content_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _CONTENT_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def _endpoint_catalog() -> List[Dict[str, Any]]:
    """The endpoint list the console renders. One dict per endpoint."""
    return [
        # --- 运动动作 ---
        {
            "group": "motion",
            "method": "POST",
            "path": "/api/core/motion/v1/actions",
            "title": "提交动作",
            "safety": "SAFETY_CRITICAL",
            "poll": "action",
            "description": (
                "唯一入口提交一切运动行为（思岚异步动作模式）：返回 action_id 后立即执行，"
                "客户端轮询 actions/{id} 获取状态。单槽位：有动作在跑时再提交返回 400 "
                "code=ROBOT_BUSY。action_name 同时接受 g1.actions.* 与 slamtec.agent.actions.*。"
            ),
            "params": [
                {"name": "action_name", "type": "string", "unit": "", "required": True, "desc": "动作工厂名，如 g1.actions.MoveToAction"},
                {"name": "options", "type": "object", "unit": "", "required": True, "desc": "按动作类型各异的参数（见样例）"},
            ],
            "sample": {
                "action_name": "g1.actions.MoveToAction",
                "options": {"target": {"x": 0.5, "y": 0.0}, "yaw": 0.0, "with_yaw": True, "reach_threshold": 0.35, "timeout_s": 60},
            },
        },
        {
            "group": "motion", "method": "GET", "path": "/api/core/motion/v1/actions/:current",
            "title": "查询当前动作", "safety": "SAFE_READ", "poll": None,
            "description": "轮询当前正在执行的动作。空闲时返回 HTTP 404，body 为 JSON 字符串 \"Action Not Found\"——这是正常控制流，不是错误。",
            "params": [], "sample": None,
        },
        {
            "group": "motion", "method": "GET", "path": "/api/core/motion/v1/actions/{action_id}",
            "title": "按 id 查询动作", "safety": "SAFE_READ", "poll": None,
            "description": "查询任意历史动作（保留最近 100 条）。action_id 为整数。",
            "params": [{"name": "action_id", "type": "int", "unit": "", "required": True, "desc": "动作整数 id（提交动作返回的 action_id）"}],
            "sample": None,
        },
        {
            "group": "motion", "method": "DELETE", "path": "/api/core/motion/v1/actions/:current",
            "title": "取消当前动作", "safety": "ACTION", "poll": None,
            "description": "取消当前动作 → status 4, result -2（被取消）。",
            "params": [], "sample": None,
        },
        {
            "group": "motion", "method": "GET", "path": "/api/core/motion/v1/action-factories",
            "title": "动作工厂列表", "safety": "SAFE_READ", "poll": None,
            "description": "返回全部动作类型及每类的 options JSON Schema。",
            "params": [], "sample": None,
        },
        # --- 建图录制 ---
        {
            "group": "mapping", "method": "POST", "path": "/api/core/slam/v1/mapping/:start",
            "title": "开始建图", "safety": "ACTION", "poll": None,
            "description": "开始建图会话：停掉导航栈、启动 FAST-LIO 建图栈（丢失当前定位）。"
                           "建图期间导航/导览被 mapping_running 互锁拒绝，但 MoveAction 速度遥控可用——"
                           "用它驱动机器人慢速(<0.5m/s)走遍场地。真机也可用手柄遥控。",
            "params": [], "sample": {},
        },
        {
            "group": "mapping", "method": "GET", "path": "/api/core/slam/v1/mapping",
            "title": "建图状态", "safety": "SAFE_READ", "poll": None,
            "description": "建图会话状态：active / 已运行时长 / 备注（launch 崩溃会在这里报出来）。",
            "params": [], "sample": None,
        },
        {
            "group": "mapping", "method": "POST", "path": "/api/core/slam/v1/mapping/:finish",
            "title": "结束建图并入库", "safety": "ACTION", "poll": None,
            "description": "结束建图：先存 2D 栅格（/projected_map → map_saver），再停建图栈"
                           "（触发 map.pcd/ground_map.pcd 自动保存），最后打包成 .g1map 注册进地图库。"
                           "之后 :select 新地图并重定位即可导航。map_id 不填则用时间戳自动命名。",
            "params": [
                {"name": "map_id", "type": "string", "unit": "", "required": False, "desc": "新地图 id（默认 map-<时间戳>）"},
                {"name": "name", "type": "string", "unit": "", "required": False, "desc": "人类可读名"},
            ],
            "sample": {"map_id": "my-room", "name": "我的场地"},
        },
        {
            "group": "mapping", "method": "POST", "path": "/api/core/slam/v1/mapping/:cancel",
            "title": "放弃建图", "safety": "ACTION", "poll": None,
            "description": "停止建图栈、不保存不入库。",
            "params": [], "sample": {},
        },
        # --- 地图 ---
        {
            "group": "map", "method": "GET", "path": "/api/core/slam/v1/maps",
            "title": "列出地图库", "safety": "SAFE_READ", "poll": None,
            "description": "列出地图库（服务端目录 ~/.g1_api/maps/<map_id>/）。",
            "params": [], "sample": None,
        },
        {
            "group": "map", "method": "POST", "path": "/api/core/slam/v1/maps",
            "title": "上传地图包", "safety": "ACTION", "poll": None,
            "description": "选 .g1map（tar.gz）点 Execute 即上传，校验 checksums 后入库。",
            "params": [{"name": "body", "type": "binary", "unit": "", "required": True, "desc": ".g1map 文件字节流"}],
            "sample": None,
            "upload": {"accept": ".g1map", "query": []},
        },
        {
            "group": "map", "method": "GET", "path": "/api/core/slam/v1/maps/{map_id}/package",
            "title": "下载地图包", "safety": "SAFE_READ", "poll": None,
            "description": "下载完整 .g1map 备份。",
            "params": [{"name": "map_id", "type": "string", "unit": "", "required": True, "desc": "地图 uuid"}],
            "sample": None,
        },
        {
            "group": "map", "method": "GET", "path": "/api/core/slam/v1/maps/:current",
            "title": "当前选中地图", "safety": "SAFE_READ", "poll": None,
            "description": "当前选中地图的 manifest。",
            "params": [], "sample": None,
        },
        {
            "group": "map", "method": "POST", "path": "/api/core/slam/v1/maps/{map_id}/:select",
            "title": "选择地图", "safety": "ACTION", "poll": None,
            "description": "选择地图，返回 {selected:true, requires_relocalization:true}。切换后必须重定位才能导航。",
            "params": [{"name": "map_id", "type": "string", "unit": "", "required": True, "desc": "地图 uuid"}],
            "sample": None,
        },
        {
            "group": "map", "method": "DELETE", "path": "/api/core/slam/v1/maps/{map_id}",
            "title": "删除地图", "safety": "ACTION", "poll": None,
            "description": "删除地图；当前选中的拒绝删除（409）。",
            "params": [{"name": "map_id", "type": "string", "unit": "", "required": True, "desc": "地图 uuid"}],
            "sample": None,
        },
        {
            "group": "map", "method": "GET", "path": "/api/core/slam/v1/maps/{map_id}/grid",
            "title": "读取 2D 栅格", "safety": "SAFE_READ", "poll": None,
            "description": "该地图的 grid.pgm 原始字节（P5）。地图编辑器（/sdk/map-editor）用它加载画布。",
            "params": [{"name": "map_id", "type": "string", "unit": "", "required": True, "desc": "地图 id"}],
            "sample": None,
        },
        {
            "group": "map", "method": "PUT", "path": "/api/core/slam/v1/maps/{map_id}/grid",
            "title": "保存 2D 栅格（同名覆盖）", "safety": "ACTION", "poll": None,
            "description": "覆盖保存该地图的 grid.pgm（尺寸必须不变；黑0=障碍/白254=自由/灰205=未知）。"
                           "保存后需重新 :select 该图导航栈才会加载新栅格。建议用地图编辑器操作。",
            "params": [{"name": "body", "type": "binary", "unit": "", "required": True, "desc": "P5 PGM 字节流"}],
            "sample": None,
        },
        {
            "group": "map", "method": "PUT", "path": "/api/core/slam/v1/maps/{map_id}/tour-points",
            "title": "写航迹点（写回 manifest）", "safety": "ACTION", "poll": None,
            "description": "把航迹点写进该地图的 manifest（同名覆盖，随 .g1map 下载走）。"
                           "动作支持 executor 字段：\"onboard\"(默认，机载手臂/灵巧手管线) 或 "
                           "\"external\"(自定义外部执行器；未配置 tour.external_executor_url 时导览中记日志跳过)。",
            "params": [{"name": "map_id", "type": "string", "unit": "", "required": True, "desc": "地图 id"}],
            "sample": {"points": [
                {"name": "大厅", "x": -3.0, "y": 0.0, "yaw": 3.14,
                 "actions": [{"executor": "onboard", "type": "arm", "id": 26}],
                 "tts_text": "欢迎来到大厅", "dwell_s": 0.5,
                 "reach_threshold": 0.35, "yaw_threshold": 0.5},
                {"name": "展台A", "x": -7.0, "y": -2.0, "yaw": -1.57,
                 "actions": [{"executor": "external", "type": "my-custom-action", "params": {"speed": 1}}],
                 "tts_text": "这里是展台A", "dwell_s": 0.5},
            ]},
        },
        # --- 定位 ---
        {
            "group": "localization", "method": "GET", "path": "/api/core/slam/v1/localization/pose",
            "title": "当前位置", "safety": "SAFE_READ", "poll": None,
            "description": "返回 {x,y,z,yaw,pitch,roll}，来自 TF map→body。",
            "params": [], "sample": None,
        },
        {
            "group": "localization", "method": "POST", "path": "/api/core/slam/v1/localization/:relocalize",
            "title": "重定位", "safety": "ACTION", "poll": None,
            "description": "body {x,y,yaw}（z/roll/pitch 默认 0）→ 调 ROS 服务 /slam_reloc，成功后清除 localization_not_initialized 互锁。",
            "params": [
                {"name": "x", "type": "number", "unit": "m", "required": True, "desc": "初始位姿 X"},
                {"name": "y", "type": "number", "unit": "m", "required": True, "desc": "初始位姿 Y"},
                {"name": "yaw", "type": "number", "unit": "rad", "required": True, "desc": "初始朝向"},
            ],
            "sample": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        },
        {
            "group": "localization", "method": "GET", "path": "/api/core/slam/v1/localization/status",
            "title": "定位状态", "safety": "SAFE_READ", "poll": None,
            "description": "返回 {initialized, map_id, note}。FAST-LIO 无 0-100 质量分，不编造质量数值。",
            "params": [], "sample": None,
        },
        # --- 语音 ---
        {
            "group": "voice", "method": "POST", "path": "/api/core/voice/v1/tts",
            "title": "TTS 语音播报", "safety": "ACTION", "poll": None,
            "description": "G1 的 TTS 没有完成回调，时长用经验公式 len(text)*0.195 秒估算；wait=true 时服务端 sleep 估算时长后返回。",
            "params": [
                {"name": "text", "type": "string", "unit": "", "required": True, "desc": "要播报的中文文本"},
                {"name": "speaker_id", "type": "int", "unit": "", "required": False, "desc": "说话人 id，默认 0"},
                {"name": "wait", "type": "bool", "unit": "", "required": False, "desc": "是否等待播报完成"},
            ],
            "sample": {"text": "你好，我是深蓝机器人", "speaker_id": 0, "wait": False},
        },
        {
            "group": "voice", "method": "GET", "path": "/api/core/voice/v1/volume",
            "title": "查询音量", "safety": "SAFE_READ", "poll": None,
            "description": "0-100。", "params": [], "sample": None,
        },
        {
            "group": "voice", "method": "PUT", "path": "/api/core/voice/v1/volume",
            "title": "设置音量", "safety": "ACTION", "poll": None,
            "description": "0-100，映射 VoiceSDK.set_volume。",
            "params": [{"name": "volume", "type": "int", "unit": "", "required": True, "desc": "0-100"}],
            "sample": {"volume": 100},
        },
        {
            "group": "voice", "method": "PUT", "path": "/api/core/voice/v1/led",
            "title": "设置头部 LED", "safety": "ACTION", "poll": None,
            "description": "头部 LED，{r,g,b} 0-255。本体状态指示会覆盖自定义色，"
                           "hold_s>0 表示每秒重发保持该颜色 hold_s 秒（新请求取消旧保持）。",
            "params": [
                {"name": "r", "type": "int", "unit": "", "required": True, "desc": "0-255"},
                {"name": "g", "type": "int", "unit": "", "required": True, "desc": "0-255"},
                {"name": "b", "type": "int", "unit": "", "required": True, "desc": "0-255"},
                {"name": "hold_s", "type": "float", "unit": "s", "required": False, "desc": "保持时长；0=只设一次"},
            ],
            "sample": {"r": 0, "g": 255, "b": 0, "hold_s": 10},
        },
        # --- 手臂 ---
        {
            "group": "arm", "method": "GET", "path": "/api/core/arm/v1/action-list",
            "title": "手臂动作列表", "safety": "SAFE_READ", "poll": None,
            "description": "真机启动时调一次 GetActionList() 缓存返回（权威来源）。",
            "params": [], "sample": None,
        },
        {
            "group": "arm", "method": "POST", "path": "/api/core/arm/v1/actions",
            "title": "执行手臂动作", "safety": "ACTION", "poll": None,
            "description": "ExecuteAction(id)；执行完后自动 ExecuteAction(99)（release 复位）并等 3 秒。",
            "params": [{"name": "action_id", "type": "int", "unit": "", "required": True, "desc": "动作 id（见 action-list）"}],
            "sample": {"action_id": 26},
        },
        # --- 外部动作（回放库）---
        {
            "group": "extact", "method": "GET", "path": "/api/core/motion/v1/external-actions",
            "title": "外部动作列表", "safety": "SAFE_READ", "poll": None,
            "description": "已上传的回放动作库（id/名称/帧数/时长）。",
            "params": [], "sample": None,
        },
        {
            "group": "extact", "method": "POST", "path": "/api/core/motion/v1/external-actions",
            "title": "上传外部动作 (.npy)", "safety": "ACTION", "poll": None,
            "description": "选 .npy（N×≥14 双臂轨迹）+ 填参数点 Execute 即入库；frequency 必须=录制频率。同名覆盖更新。库存放在挂载卷，容器重启不丢。",
            "params": [
                {"name": "body", "type": "binary", "unit": "", "required": True, "desc": ".npy 文件字节流"},
                {"name": "name", "type": "str", "unit": "", "required": True, "desc": "动作名（转成 id）"},
                {"name": "frequency", "type": "float", "unit": "Hz", "required": True, "desc": "回放频率=录制频率"},
                {"name": "velocity_limit", "type": "float", "unit": "rad/s", "required": False, "desc": "关节限速，新轨迹先用 5 试"},
                {"name": "description", "type": "str", "unit": "", "required": False, "desc": "备注"},
            ],
            "sample": None,
            "upload": {"accept": ".npy", "query": ["name", "frequency", "velocity_limit", "description"]},
        },
        {
            "group": "extact", "method": "POST", "path": "/api/core/motion/v1/external-actions/{action_id}/:execute",
            "title": "执行外部动作", "safety": "ACTION", "poll": None,
            "description": "同步执行：响应返回=回放结束。执行期挂 external_motion_running 互锁（挡官方手臂动作与导航）。需要执行器在跑（robot_extexec.sh）。",
            "params": [{"name": "action_id", "type": "str", "unit": "", "required": True, "desc": "库里的动作 id"}],
            "sample": None,
        },
        {
            "group": "extact", "method": "DELETE", "path": "/api/core/motion/v1/external-actions/{action_id}",
            "title": "删除外部动作", "safety": "ACTION", "poll": None,
            "description": "从库中删除 .npy 与元数据。",
            "params": [{"name": "action_id", "type": "str", "unit": "", "required": True, "desc": "库里的动作 id"}],
            "sample": None,
        },
        # --- 灵巧手 ---
        {
            "group": "hand", "method": "POST", "path": "/api/core/hand/v1/command",
            "title": "灵巧手指令", "safety": "ACTION", "poll": None,
            "description": "灵巧手 TCP 5678，指令 1-19（1-6 左手、7-12 右手、13-19 双手）。",
            "params": [{"name": "cmd", "type": "string", "unit": "", "required": True, "desc": "\"1\"..\"19\" 或 s 查状态"}],
            "sample": {"cmd": "7"},
        },
        # --- 系统 ---
        {
            "group": "system", "method": "GET", "path": "/api/core/system/v1/robot/info",
            "title": "机器人信息", "safety": "SAFE_READ", "poll": None,
            "description": "厂商/型号(Unitree G1)/软件版本/device_id/模式(mock/real)。",
            "params": [], "sample": None,
        },
        {
            "group": "system", "method": "GET", "path": "/api/core/system/v1/capabilities",
            "title": "能力列表", "safety": "SAFE_READ", "poll": None,
            "description": "各子系统就绪标志（navigation, tts, arm, hand, tour…）。enabled=false 表示未就绪而非不存在。",
            "params": [], "sample": None,
        },
        {
            "group": "system", "method": "GET", "path": "/api/core/system/v1/robot/health",
            "title": "健康状态", "safety": "SAFE_READ", "poll": None,
            "description": "{hasWarning,hasError,hasFatal,baseError:[...],interlocks:[命名互锁]}。命名互锁附解除方法。",
            "params": [], "sample": None,
        },
        # --- 导览 ---
        {
            "group": "tour", "method": "GET", "path": "/api/tour/v1/tours",
            "title": "导览定义", "safety": "SAFE_READ", "poll": None,
            "description": "当前地图的导览定义（导览点随地图走，存在 manifest 里）。",
            "params": [], "sample": None,
        },
        {
            "group": "tour", "method": "PUT", "path": "/api/tour/v1/tours",
            "title": "更新导览定义", "safety": "ACTION", "poll": None,
            "description": "回写当前地图 manifest 的 tour_points。",
            "params": [
                {"name": "points", "type": "array", "unit": "", "required": True, "desc": "导览点列表"},
                {"name": "on_failure", "type": "string", "unit": "", "required": False, "desc": "retry_once|skip|abort"},
            ],
            "sample": {"points": [
                {"name": "点1", "x": 0.5, "y": 0.0, "yaw": 0.0, "actions": [{"type": "arm", "id": 26}], "tts_text": "这是第一站", "dwell_s": 0.0},
                {"name": "点2", "x": 1.0, "y": 0.0, "yaw": 0.0, "actions": [{"type": "hand", "cmd": "7"}], "tts_text": "这是第二站", "dwell_s": 0.0},
            ], "on_failure": "retry_once"},
        },
        {
            "group": "tour", "method": "POST", "path": "/api/tour/v1/points/{index}/:test",
            "title": "试跑单点（全流程）", "safety": "ACTION", "poll": "/api/tour/v1/tours/:current",
            "description": "试跑当前地图第 index 个航迹点（0 起）。默认原地排练：只动作+TTS+停留、不走动；加 ?nav=1 才先导航过去（全流程）。以单点导览执行：/tours/:current 轮询、/tours/:stop 中止。地图编辑器每点面板有两个试跑按钮。",
            "params": [{"name": "index", "type": "int", "unit": "", "required": True, "desc": "航迹点序号（0 起）"}],
            "sample": None,
        },
        {
            "group": "tour", "method": "POST", "path": "/api/tour/v1/tours/:start",
            "title": "开始导览", "safety": "ACTION", "poll": "tour",
            "description": "前置检查：地图已选、已重定位、无致命健康、ALLOW_NAVIGATION=true，任一不满足返回 409 + 互锁名。",
            "params": [], "sample": None,
        },
        {
            "group": "tour", "method": "POST", "path": "/api/tour/v1/tours/:pause",
            "title": "暂停导览", "safety": "ACTION", "poll": None, "description": "暂停。", "params": [], "sample": None,
        },
        {
            "group": "tour", "method": "POST", "path": "/api/tour/v1/tours/:resume",
            "title": "恢复导览", "safety": "ACTION", "poll": None, "description": "恢复。", "params": [], "sample": None,
        },
        {
            "group": "tour", "method": "POST", "path": "/api/tour/v1/tours/:stop",
            "title": "停止导览", "safety": "ACTION", "poll": None, "description": "停止并取消当前动作、停机器人。", "params": [], "sample": None,
        },
        {
            "group": "tour", "method": "GET", "path": "/api/tour/v1/tours/:current",
            "title": "导览状态", "safety": "SAFE_READ", "poll": "tour",
            "description": "整体状态 + 当前点序号 + 每点结果列表。",
            "params": [], "sample": None,
        },
        {
            "group": "tour", "method": "GET", "path": "/api/tour/v1/events",
            "title": "导览事件", "safety": "SAFE_READ", "poll": None,
            "description": "轮询导览事件流（point_reached / tts_started / action_done / point_failed…）。",
            "params": [{"name": "from_cursor", "type": "string", "unit": "", "required": False, "desc": "续读游标"}],
            "sample": None,
        },
    ]


GROUP_META = [
    ("motion", "运动动作"),
    ("mapping", "建图录制"),
    ("map", "地图"),
    ("localization", "定位"),
    ("voice", "语音"),
    ("arm", "手臂"),
    ("extact", "外部动作"),
    ("hand", "灵巧手"),
    ("system", "系统"),
    ("tour", "导览"),
]


@router.get(CONSOLE_PREFIX, include_in_schema=False)
async def console_page() -> Response:
    body = _read_bytes(os.path.join(STATIC_DIR, "index.html"))
    if body is None:
        return Response(content=b"SDK console page missing", status_code=500, media_type="text/plain")
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get(CONSOLE_PREFIX + "/", include_in_schema=False)
async def console_page_slash() -> Response:
    return await console_page()


@router.get(CONSOLE_PREFIX + "/map-editor", include_in_schema=False)
async def map_editor_page() -> Response:
    body = _read_bytes(os.path.join(STATIC_DIR, "mapedit.html"))
    if body is None:
        return Response(content=b"map editor page missing", status_code=500, media_type="text/plain")
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get(CONSOLE_PREFIX + "/meta.json", include_in_schema=False)
async def console_meta(request: Request) -> Response:
    config = getattr(request.app.state, "config", None)
    payload = {
        "title": "G1 SDK 调试台",
        "groups": [{"id": gid, "name": name} for gid, name in GROUP_META],
        "endpoints": _endpoint_catalog(),
        "mode": getattr(config, "mode", None).mode if config is not None else "mock",
        "safety_flags": {
            "allow_motion": bool(config.safety.allow_motion),
            "allow_navigation": bool(config.safety.allow_navigation),
            "allow_map_write": bool(config.safety.allow_map_write),
            "allow_arm": bool(config.safety.allow_arm),
            "allow_hand": bool(config.safety.allow_hand),
            "allow_voice": bool(config.safety.allow_voice),
            "allow_tour": bool(config.safety.allow_tour),
        } if config is not None else {},
    }
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.get(CONSOLE_PREFIX + "/static/{asset_path:path}", include_in_schema=False)
async def console_asset(asset_path: str) -> Response:
    resolved = _safe_asset_path(asset_path)
    if resolved is None:
        return Response(content=b"Not Found", status_code=404, media_type="text/plain")
    body = _read_bytes(resolved)
    if body is None:
        return Response(content=b"Not Found", status_code=404, media_type="text/plain")
    return Response(
        content=body,
        media_type=_content_type_for(resolved),
        headers={"Cache-Control": "no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"},
    )


def install_console(app: Any, config: AppConfig) -> None:
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index_path):
        return
    app.include_router(router)
