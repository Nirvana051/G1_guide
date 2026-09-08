# g1_api — 思岚模式核心 API + SDK 调试网站 + 导览应用（Unitree G1）

为 Unitree G1 人形机器人独立实现的一套思岚（Slamtec）模式机器人系统，三层交付物：

1. **核心 REST API**（思岚异步动作模式）：move / moveto / rotate / rotateto、`.g1map` 地图包上传/下载/选择、重定位、导航、系统健康、TTS、手臂动作、灵巧手、异常处理。
2. **SDK 调试网站**（`/sdk`）：每个 SDK 接口是一个折叠页，含中文说明 + 预填样例 + Execute 按钮 + 真实 response。
3. **导览应用**（`/api/tour/v1/*`）：选图 → 重定位 → 顺序到点（朝向正确）→ 每个点**同时**执行机器人动作和播放语音。

默认后端是 **mock**（完整仿真，导航按速度插值走位）；真机适配器只在 `G1_API_MODE=real` 时加载，`import g1_api` 在无 ROS 的机器上永不失败。

---

## 0. 快速开始（mock，开发机）

```bash
cd /home/g1/workspace/project/concierge/G1/st_ws/g1_api
python3 -m g1_api
# 监听 127.0.0.1:1448
```

另开终端实测：

```bash
curl -s http://127.0.0.1:1448/healthz
curl -s http://127.0.0.1:1448/api/core/motion/v1/action-factories
```

浏览器打开 **http://127.0.0.1:1448/sdk** 使用折叠页调试台。

> 默认所有 `ALLOW_*` 安全开关为 **false**，任何会动/发声/动手的命令都会被
> `409 SAFETY_INTERLOCK` 拒绝并给出互锁名。演示完整流程前先打开开关（见下）。

## 1. 安全红线

- 开发全程在本机用 mock 适配器，**绝不联网到任何机器人**（`192.168.123.*` 等）。
- 默认 `G1_API_MODE=mock`；真机适配器只有显式 `G1_API_MODE=real` 才加载。
- 所有让机器人动的能力默认经安全闸门拒绝（`ALLOW_*` 全部默认 false）。
- 命名互锁：`roscore_down` / `move_base_down` / `velocity_bridge_down` /
  `dds_unreachable` / `map_not_selected` / `localization_not_initialized` /
  `motion_not_allowed` / `tour_running`。每个 `409` 都会列出互锁名与解除方法。

### 演示时打开开关（仅 mock）

```bash
G1_API_SAFETY_ALLOW_MOTION=true \
G1_API_SAFETY_ALLOW_NAVIGATION=true \
G1_API_SAFETY_ALLOW_MAP_WRITE=true \
G1_API_SAFETY_ALLOW_ARM=true \
G1_API_SAFETY_ALLOW_HAND=true \
G1_API_SAFETY_ALLOW_VOICE=true \
G1_API_SAFETY_ALLOW_TOUR=true \
python3 -m g1_api
```

## 2. 目录结构

```
g1_api/
├── config/default.yaml        # 分层配置：默认 → YAML → G1_API_* 环境变量
├── g1_api/
│   ├── models/                # 纯 stdlib 规范模型（单位入字段名）
│   ├── core/                  # 动作状态机、单槽位任务管理器、事件总线、安全闸门、时钟
│   ├── state/                 # 动作生命周期状态机
│   ├── adapters/mock/         # 完整仿真（默认）
│   ├── adapters/g1/           # 真机接入（惰性导入，含 navigation_sdk 副本 + launch 管理器）
│   ├── services/              # 用例逻辑（返回内部模型，不外泄 wire JSON）
│   ├── tour/                  # 导览执行器（坐在核心 API 之上）
│   ├── gateway/               # FastAPI 网关（鉴权/校验/problem+json）
│   └── console/               # /sdk 折叠页调试台
├── tests/                     # 192 个测试（四条铁律、py38、安全、状态机、地图包、导览验收）
├── deploy/                    # g1_api_navigation.launch（参数化，绕开课程硬编码地图路径）
└── docs/                      # API.md / DEPLOY_PC2.md / TODO.md
```

## 3. 测试

```bash
cd /home/g1/workspace/project/concierge/G1/st_ws/g1_api
python3 -m pytest tests -q          # 192 passed（实测）
```

四条铁律各有一个测试守护（`tests/unit/test_layering.py`、
`tests/unit/test_py38_compat.py`、`tests/unit/test_safety_gate.py`、
`tests/integration/test_g1_import.py`）：分层禁止 import fastapi/pydantic、全库
Python 3.8 语法、一切状态变更过安全闸门且 `ALLOW_*` 默认 false、默认 mock 且真机
适配器惰性导入。

## 4. 真机部署

真机 PC2 的完整 runbook 在 [`docs/DEPLOY_PC2.md`](docs/DEPLOY_PC2.md)。关键点：

- 容器 `AbotClaw` 内 conda env `g1_agent`(3.10) + ROS1 系统 py3.8，端口 **1448**。
- `source /opt/ros/noetic/setup.bash` + `source devel/setup.bash`，`source config.env`。
- 前置进程：`g1_control_vel.py eth0`、hand server（TCP 5678）、以及 g1_api 自管的
  `g1_api_navigation.launch`。
- **导航前机器人必须处于走跑模式**（无已知 API 可查，作为文档性前置）。

## 5. 文档

- [`docs/API.md`](docs/API.md) — 全部端点与语义（含思岚怪癖的保留与修正）。
- [`docs/DEPLOY_PC2.md`](docs/DEPLOY_PC2.md) — 真机 runbook + 冒烟清单。
- [`docs/TODO.md`](docs/TODO.md) — 未做的事全列，不藏。
