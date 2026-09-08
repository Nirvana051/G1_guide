# st_ws — Unitree G1 导览机器人工作空间

思岚（Slamtec）模式的 Unitree G1 人形机器人系统。一套 REST API 把「建图 → 选图 →
重定位 → 导航到点 → 到点做动作+说话」做成可编程的接口，配一个网页调试台和一个导览应用。

本工作空间是**开发机侧的完整镜像**，真机侧对应 `/home/unitree/abotclaw_nv`。

| | |
|---|---|
| 目标平台 | Unitree G1（29 DoF，双臂 + LinkerHand 灵巧手） |
| 机载计算 | PC2（Jetson，ARM64），容器 `AbotClaw` |
| 软件栈 | ROS1 Noetic + FAST-LIO2 + move_base/TEB + Unitree DDS（`unitree_sdk2py`） |
| 主服务 | `g1_api`，端口 **1448** |
| 开发姿态 | **默认 mock 全仿真**，开发机全程不碰真机 |

---

## 1. 快速开始

### 开发机（mock，零风险）

```bash
cd /home/g1/workspace/project/concierge/G1/st_ws/g1_api
python3 -m g1_api                      # 监听 127.0.0.1:1448
```

浏览器打开 **http://127.0.0.1:1448/sdk** —— 每个接口一个折叠页，含中文说明、
预填样例、Execute 按钮和真实响应。地图编辑器在 `/sdk/map-editor`。

> ⚠️ 需要 Python 3.8–3.10 且装了 `fastapi/pydantic/uvicorn/PyYAML`。本机 conda base
> 是 **Python 3.14**，直接跑会失败——见 [§7 环境要求](#7-环境要求)。

默认所有 `ALLOW_*` 安全开关为 **false**，任何会动/发声/动手的命令都会被
`409 SAFETY_INTERLOCK` 拒绝。演示完整流程要先开开关：

```bash
G1_API_SAFETY_ALLOW_MOTION=true G1_API_SAFETY_ALLOW_NAVIGATION=true \
G1_API_SAFETY_ALLOW_MAP_WRITE=true G1_API_SAFETY_ALLOW_ARM=true \
G1_API_SAFETY_ALLOW_HAND=true G1_API_SAFETY_ALLOW_VOICE=true \
G1_API_SAFETY_ALLOW_TOUR=true python3 -m g1_api
```

### 真机（PC2）

全部走 [g1_api/deploy/](g1_api/deploy/) 里的脚本，五个终端，详见
[§4 真机拓扑](#4-真机拓扑两张网五个终端) 和上机手册。

```bash
ssh unitree@192.168.220.8 && docker exec -it AbotClaw bash
source /home/unitree/abotclaw_nv/g1_api/deploy/robot_env.sh && roscore   # 终端①
source .../deploy/robot_velbridge.sh                                     # 终端② 速度桥
source .../deploy/robot_extexec.sh                                       # 终端③ 外部动作执行器
source .../deploy/robot_g1api.sh                                         # 终端④ g1_api
```

---

## 2. 目录地图

「归属」一列决定你能不能随便改：**自研**＝本项目写的，**课程**＝上游课程代码
（改动要留痕，真机上可能另有版本），**vendor**＝第三方源码，原则上不动。

| 路径 | 是什么 | 归属 | 权威文档 |
|---|---|---|---|
| [g1_api/](g1_api/) | **核心交付物**。REST API + `/sdk` 调试台 + 导览应用 + 真机适配器 + 部署脚本 | 自研 | [g1_api/README.md](g1_api/README.md) |
| [g1_replay_min/](g1_replay_min/) | 双臂动作回放最小版（2 MB，替代 9.4 GB 的 conda 全量版）；`action_server.py` 是 g1_api 的外部动作执行器 | 自研 | [g1_replay_min/README.md](g1_replay_min/README.md) |
| [hardware/](hardware/) | 相机服务器（RealSense→TCP 8765）、灵巧手 server（TCP 5678）、LinkerHand SDK | 自研 + vendor | [hardware/doc/README.md](hardware/doc/README.md) |
| [navigate/WK/](navigate/WK/) | 课程导航栈 `G1Nav2D`：fastlio2、livox 驱动、movebase、pointcloud_to_laserscan、地图编辑插件 | 课程 | [navigate/WK/README.md](navigate/WK/README.md) |
| [navigate/AeroMaze_ws/](navigate/AeroMaze_ws/) | AeroMaze planner overlay（TEB 走廊约束版），`G1_API_PLANNER=aero` 时用它 | 课程 fork | — |
| [navigate/Livox-SDK2/](navigate/Livox-SDK2/) | Livox MID360 雷达 SDK | vendor | 上游 README |
| [navigate/map/](navigate/map/) | 建图产物落地目录（`map.pcd` / `ground_map.pcd` / `mymap.pgm+yaml` / `key_poses.txt`）**map_builder 写死的输出路径** | 数据 | — |
| [unitree_sdk2_python/](unitree_sdk2_python/) | Unitree DDS SDK + 自带 CycloneDDS 源码（`cyclonedds/install` 是编译产物） | vendor | — |
| [robot_client/](robot_client/) | 课程 `agent_server`（FastAPI，端口 8888）+ `robot_sdk`。**g1_api 不依赖它**，仅作参考与共存 | 课程 | [robot_client/unitree_G1/README.md](robot_client/unitree_G1/README.md) |
| [g1_api_maps/](g1_api_maps/) | **地图库**（g1_api 持久化目录）。当前一张图 `delta`，含 3 个导览点 | 数据 | — |
| [g1_api_external_actions/](g1_api_external_actions/) | **外部动作库**（回放轨迹）。`<id>.npy` + `<id>.json` 成对；现有 `e1/left/right/talk_rise/tour01_fit/tour02_fit/tour04_fit` | 数据 | — |
| [bags/](bags/) | 从真机拉回的 rosbag（调试速度桥时自动录制） | 数据 | — |
| [config.env](config.env) | 全局环境变量：网卡、IP、CycloneDDS、模型路径。**换机器只改「机器配置」区** | 自研 | 文件内注释 |
| [Dockerfile.abotclaw](Dockerfile.abotclaw) | `AbotClaw` 镜像定义（Ubuntu 20.04 + Noetic + miniconda，两个 conda env） | 自研 | — |
| [example/tts_test.py](example/tts_test.py) | 最小 TTS 冒烟脚本 | 自研 | — |

### g1_api 内部分层

```
g1_api/
├── config/default.yaml     # 分层配置：内置默认 → YAML → G1_API_<SECTION>_<FIELD> 环境变量
├── g1_api/
│   ├── models/             # 纯 stdlib 规范模型（单位写进字段名）
│   ├── core/               # 动作状态机、单槽位任务管理器、事件总线、安全闸门、时钟
│   ├── state/              # 动作生命周期
│   ├── adapters/mock/      # 完整仿真（默认；导航按速度插值走位）
│   ├── adapters/g1/        # 真机：ros_bridge / dds_bridge / launch_manager / navigation_sdk
│   ├── services/           # 用例逻辑（返回内部模型，不外泄 wire JSON）
│   ├── tour/               # 导览执行器（坐在核心 API 之上）
│   ├── gateway/            # FastAPI 网关（校验 / problem+json）
│   └── console/            # /sdk 折叠页调试台 + /sdk/map-editor
├── tests/                  # 17 个测试文件；四条铁律各有守护测试
├── deploy/                 # 五个 robot_*.sh + laptop_rviz.sh + 参数化 launch + 速度桥
└── docs/                   # system_bible / deploy_handbook / quickstart / API.md / TODO.md
```

**四条架构铁律**（各有一个测试守护，别绕）：

| 铁律 | 守护测试 |
|---|---|
| 分层隔离：models/core/services 禁止 import fastapi/pydantic | `tests/unit/test_layering.py` |
| 全库 Python 3.8 语法（真机 ROS1 是 py3.8） | `tests/unit/test_py38_compat.py` |
| 一切状态变更过安全闸门，`ALLOW_*` 默认 false | `tests/unit/test_safety_gate.py` |
| 默认 mock，真机适配器惰性导入（无 ROS 机器上 `import g1_api` 也不失败） | `tests/integration/test_g1_import.py` |

---

## 3. API 速览

服务端口 **1448**，错误 body 用 RFC 9457 `application/problem+json`。
完整语义见 [g1_api/docs/API.md](g1_api/docs/API.md)。

| 领域 | 前缀 | 关键点 |
|---|---|---|
| 动作 | `/api/core/motion/v1/actions` | **单槽位异步**：提交返回 `action_id`，轮询查进度。`status 0→1→(3)→4`（没有 2）；`result` 只在 `status==4` 有意义 |
| 地图包 | `/api/core/slam/v1/maps` | `.g1map` = tar.gz（manifest + map.pcd + ground_map.pcd + grid.pgm/yaml），上传校验 checksum |
| 建图 | `/api/core/slam/v1/mapping/:start\|:finish\|:cancel` | 会话独占 SLAM 栈，一次只允许一个 |
| 定位 | `/api/core/slam/v1/localization/:relocalize` | 选图后**必须**重定位；FAST-LIO 没有 0–100 质量分 |
| 系统 | `/api/core/system/v1/robot/health` | 返回 `interlocks[]`，是排障第一站 |
| 语音/手臂/手 | `/api/core/{voice,arm,hand}/v1/…` | 不占动作槽位。手臂动作表以真机 `GET /arm/v1/action-list` 为准 |
| 导览 | `/api/tour/v1/tours/…` | `:start/:pause/:resume/:stop`，`/events` 可续读事件流 |

四个动作工厂：`MoveAction`（速度遥控，**不避障，最危险**）、`MoveToAction`、
`RotateAction`（相对）、`RotateToAction`（绝对）。

**两个刻意保留的思岚怪癖**：`GET /actions/:current` 空闲时返回 **404**（正常控制流，
不是错误）；动作提交冲突返回 `400 "Can not create action"`（附机器可读 `code:"ROBOT_BUSY"`）。

---

## 4. 真机拓扑：两张网、五个终端

### 两张网（搞混必翻车）

| 网 | 网段 | 谁在上面 | 用途 |
|---|---|---|---|
| **机器人内部网** | `192.168.123.x`，网卡 `eth0` | PC2 `.164`、运动控制器、MID360 雷达 `.120` | DDS、雷达。**配置里一律写 eth0，别改** |
| **对外局域网** | `192.168.220.x` | PC2 `.8`（`ROBOT_IP` 默认值） | ssh、远程 RViz、浏览器访问 `/sdk` |

`robot_env.sh` 把 `ROS_MASTER_URI`/`ROS_IP` 设到**对外网**（笔记本 RViz 才能订阅），
DDS 网卡设到**内部网**。两者互不相干。

### 端口清单

| 端口 | 服务 | 起法 |
|---|---|---|
| **1448** | `g1_api` | `source deploy/robot_g1api.sh` |
| 11311 | roscore | `source deploy/robot_env.sh && roscore`（**必须长驻独立起**，见下） |
| 9100 | 外部动作执行器（`g1_replay_min/action_server.py`） | `source deploy/robot_extexec.sh` |
| 5678 | 灵巧手 server（TCP） | `hardware/scripts/start_hand_server.sh` |
| 8765 | 相机服务器（TCP，JPEG+深度） | `hardware/camera/g1_camera_server.py` |
| 8888 | 课程 `agent_server` | 已被占用，**勿用勿依赖** |

> **roscore 必须独立长驻**，不要靠 roslaunch 自动带起：那样 master 生命周期绑在
> 该 launch 上，`:select` 切图或建图 `:finish` 停栈时 master 一起死，g1_api 里已注册的
> rospy 节点不会向新 master 重注册。

### 五个终端

| 终端 | 脚本 | 作用 | 会不会让机器人动 |
|---|---|---|---|
| ① | `robot_env.sh` + `roscore` | ROS master | 否 |
| ② | `robot_velbridge.sh`（安静版）/ `robot_debugbridge.sh`（诊断+录 bag） | `/cmd_vel` → LocoClient 速度桥，50 Hz | **会**。它一起来，`/cmd_vel` 上任何速度都会真的走 |
| ③ | `robot_extexec.sh` | 外部动作执行器 :9100 | **会**（驱动双臂） |
| ④ | `robot_g1api.sh` | g1_api 主服务 | 经安全闸门 |
| ⑤ | 手动调试 / 灵巧手 server | — | 视情况 |

笔记本侧远程 RViz：`g1_api/deploy/laptop_rviz.sh mapping|nav`（自动探测本机
同网段 IP 作为 `ROS_IP`）。

### 开发机 → 真机 的部署映射

真机项目根是 `/home/unitree/abotclaw_nv`，与本工作空间同构。日常只需同步**三个目录**：

```bash
cd /home/g1/workspace/project/concierge/G1/st_ws          # 以下 scp 都从工作空间根目录执行
R=unitree@192.168.220.8:/home/unitree/abotclaw_nv
sshpass -p 123 scp -r g1_api                                    $R/
sshpass -p 123 scp -r g1_replay_min                             $R/
sshpass -p 123 scp -r navigate/AeroMaze_ws/src/AeroMazePlanner-ST-nav \
                      $R/navigate/AeroMaze_ws/src/               # 传完在容器里编一次 overlay
```

AeroMaze overlay 编译：容器内 `bash .../g1_api/deploy/robot_build_aero.sh`（产物在
挂载卷里持久化，只需编一次）。

---

## 5. 安全模型

两类互锁，`409 SAFETY_INTERLOCK` 的 `details.active_interlocks` 会直接告诉你是哪个。

**开关类**（`ALLOW_*` 全部默认 false，改完**必须重启服务**）：

| 互锁名 | 环境变量 | 拦住谁 |
|---|---|---|
| `motion_not_allowed` | `G1_API_SAFETY_ALLOW_MOTION` | `MoveAction`（速度遥控，不避障，**最危险**） |
| `navigation_not_allowed` | `G1_API_SAFETY_ALLOW_NAVIGATION` | `MoveTo`/`Rotate`/`RotateTo`、导览 `:start` |
| `map_write_not_allowed` | `G1_API_SAFETY_ALLOW_MAP_WRITE` | 上传/选图/删图、回写导览点 |
| `arm_not_allowed` / `hand_not_allowed` / `voice_not_allowed` / `tour_not_allowed` | `..._ALLOW_ARM` / `_HAND` / `_VOICE` / `_TOUR` | 手臂 / 灵巧手 / TTS+音量+LED / 导览控制 |

**状态类**（不是开关，是环境没满足）：`map_not_selected` → 先选图；
`localization_not_initialized` → 先重定位；`roscore_down`；`move_base_down`；
`velocity_bridge_down` → 起速度桥；`dds_unreachable` → 查网卡；`mapping_running`；`tour_running`。

分层上机顺序（**别跳层**，每次只开当前层要用的开关）见
[DEPLOY_PC2.md §7](g1_api/docs/DEPLOY_PC2.md)：mock 开发机 → mock 真机 → real 全关
→ 加 VOICE+ARM → 加 NAVIGATION+MAP_WRITE → 加 HAND+TOUR（+ 可选 MOTION）。
`ALLOW_MOTION` 只在**有人持急停在场**时打开。

---

## 6. 文档索引

按「你想问什么」查，不按目录查：

| 我想… | 看这个 |
|---|---|
| 今天怎么开机干活（现场 SOP） | [g1_api/docs/quickstart.html](g1_api/docs/quickstart.html) ← **权威版** |
| 理解系统怎么搭起来的（进程拓扑、两张网、全链路、速度链） | [g1_api/docs/system_bible.html](g1_api/docs/system_bible.html) |
| 全新机器从零部署 / 五终端 SOP / 互锁对照 / Day-1 剧本 | [g1_api/docs/deploy_handbook.html](g1_api/docs/deploy_handbook.html) |
| 查某个端点的确切语义 | [g1_api/docs/API.md](g1_api/docs/API.md) |
| 部署细节的纯文本备查 + 分层测试计划 | [g1_api/docs/DEPLOY_PC2.md](g1_api/docs/DEPLOY_PC2.md) |
| 还有什么没做 / 已知缺陷 | [g1_api/docs/TODO.md](g1_api/docs/TODO.md) |
| 接手这个项目 | [HANDOFF.md](HANDOFF.md) |
| 双臂回放怎么用 / 怎么换数据集 | [g1_replay_min/README.md](g1_replay_min/README.md) |
| 从遥操作采数到真机回放的完整链路 | [g1_replay_min/11_FULL_WORKFLOW.md](g1_replay_min/11_FULL_WORKFLOW.md) |
| 相机 / 灵巧手 | [hardware/doc/](hardware/doc/) |

> **quickstart.html 有三份**：[g1_api/docs/quickstart.html](g1_api/docs/quickstart.html)
> 是权威版（含 sshpass、机器人 `.bashrc` 提示）；根目录这份和 `../quickstart.html`
> 是早期手改副本，内容滞后。改文档请只改 `g1_api/docs/` 下的。

---

## 7. 环境要求

**各组件的 Python 版本要求不同，混用会失败：**

| 组件 | 要求 | 为什么 |
|---|---|---|
| `g1_api` | **3.8 – 3.10** | 真机 ROS1 Noetic 是系统 py3.8；全库锁 3.8 语法（有测试守护）。真机上跑在 conda env `g1_agent`(3.10) |
| `g1_replay_min` | **必须 3.10** | `cyclonedds 0.10.2` 只到 cp310，`pin 4.1.0` 从 cp310 起，交集只有 cp310 |
| 本机 conda base | 当前 **3.14.6** | ⚠️ **跑不了任何一个组件**。`python3 -m pytest` 会报 `No module named pytest` |

开发机上要跑 g1_api 或它的测试，先建一个 3.8–3.10 的环境：

```bash
conda create -n g1_api_dev python=3.10 -y && conda activate g1_api_dev
pip install -e "g1_api[test]"          # fastapi/pydantic/uvicorn/PyYAML + pytest/httpx
cd g1_api && python3 -m pytest tests -q
```

`g1_replay_min` 自带一键脚本（找 3.10 → 建 venv → 装依赖 → dry-run 自检）：

```bash
cd g1_replay_min && bash setup_env.sh    # 501 MB；NO_PIN=1 则 118 MB（无重力补偿）
```

真机侧环境由 [Dockerfile.abotclaw](Dockerfile.abotclaw) 定义（Ubuntu 20.04 +
Noetic + miniconda，两个 env：`g1_agent` 3.10、`linkerhand` 3.10）。**不要重打镜像**——
项目根已被 `-v` 挂载，代码放进去即自动进容器。

---

## 8. 数据与产物

| 什么 | 在哪 | 怎么产生 |
|---|---|---|
| 地图库 | [g1_api_maps/](g1_api_maps/)（真机 `G1_API_MAP_MAP_STORAGE_DIR`） | 建图会话 `:finish` 自动打包入库，或上传 `.g1map` |
| 建图原始产物 | [navigate/map/](navigate/map/) | map_builder **写死**的输出目录 |
| 导览点 | 地图包的 `manifest.json` → `tour_points[]` | `/sdk/map-editor` 图形编辑，或 `PUT /api/tour/v1/tours` |
| 外部动作（回放轨迹） | [g1_api_external_actions/](g1_api_external_actions/) | 遥操作采数 → 导出 `.npy` → 上传；见 [11_FULL_WORKFLOW.md](g1_replay_min/11_FULL_WORKFLOW.md) |
| 调试 rosbag | [bags/](bags/) | `robot_debugbridge.sh` 自动录 `/cmd_vel /slam_odom /tf /scan`，Ctrl-C 收尾 |
| 打包工具 | `g1_api/tools/pack_g1map.py` | 手工把 `navigate/map/` 打成 `.g1map` |

**建图后必做**：`mymap.yaml` 里的 `nan` 改成 `0`，否则 map_server 报错。
（`:finish` 走 API 时已处理，手动流程要自己改。）

---

## 9. 约定与红线

- **开发机全程 mock，绝不联网到任何机器人**（`192.168.123.*` / `192.168.220.*`）。
  真机操作由人类操作员在真机上执行。
- `G1_API_MODE=mock` 是默认；真机适配器只在显式 `G1_API_MODE=real` 时才加载。
- **禁止 `import rclpy`**：真实栈是 ROS1 Noetic，没有 ROS2/Nav2。课程 README
  大面积过时，别照着写。
- 网卡一律**显式配置**（`eth0`）——课程代码里 iface 默认值有三处不一致
  （eth0/eno1/enp4s0）。
- 导航前机器人**必须处于走跑模式**（无 API 可查，纯文档性前置）。
- 改 `ALLOW_*` 或任何 `G1_API_*` 配置后**必须重启服务**才生效。
- 本工作空间**不是 git 仓库**，改动没有版本历史兜底——见 [HANDOFF.md](HANDOFF.md#10-风险与债务)。
