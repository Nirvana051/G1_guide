# HANDOFF — st_ws / Unitree G1 导览机器人

交接文档。先读这份，再读 [README.md](README.md)（工作空间地图），再按需查
[g1_api/docs/](g1_api/docs/) 下的三份手册。

| | |
|---|---|
| 交接对象 | 接手 G1 导览机器人系统的开发/运维者 |
| 文档生成 | 2026-09-08 |
| 最后一次实质代码改动 | **2026-08-31**（速度桥调参 `g1_api/deploy/g1_control_vel.py`） |
| 最后一次现场数据 | **2026-09-01**（`g1_api_maps/delta` 地图 + 3 点导览定义） |
| 项目根（开发机） | `/home/g1/workspace/project/concierge/G1/st_ws` |
| 项目根（真机 PC2） | `/home/unitree/abotclaw_nv` |

---

## 1. 一分钟摘要

做出来的是一套「思岚模式」的 G1 机器人 REST API（端口 1448）＋网页调试台
（`/sdk`）＋导览应用。**核心 API 与 mock 全链路是完整的**；**真机侧已经跑到
「建图 → 选图 → 重定位 → 导航到点 → 到点做动作+说话」全流程能演示**的程度：
现场地图 `delta` 已建好，3 个导览点带英文 TTS 和双臂回放动作已配好。

**卡在哪**：终点原地旋转对准时 FAST-LIO 定位滑移（4 秒内漂 0.85 m），这是当前
最大的技术债，见 [§6 头号问题](#6-头号问题终点旋转定位滑移)。

**接手第一件事**：把工作空间纳入 git（现在**没有版本控制**），然后按
[§3](#3-接手第一天) 走一遍冒烟。

---

## 2. 当前状态：验证到哪一层

诚实分栏。「仿真验证」＝Gazebo/mock 里端到端跑通；「真机验证」＝在 G1 硬件上跑过。

| 能力 | mock | 仿真 | 真机 | 备注 |
|---|---|---|---|---|
| 核心 API 全端点（动作/地图/定位/健康/语音/手臂/手） | ✅ | ✅ | 部分 | 读端点全部真机可用 |
| `/sdk` 调试台 + `/sdk/map-editor` | ✅ | ✅ | ✅ | 浏览器直连 PC2:1448 |
| 建图会话（`/mapping` 四端点状态机） | ✅ | ✅ | ✅ | `delta` 图就是这么建的 |
| `.g1map` 打包 / 上传 / `:select` 切图（launch 重启） | ✅ | ✅ | ✅ | |
| `/slam_reloc` 重定位（含 reloc-check 轮询） | ✅ | ✅ | ✅ | |
| `MoveToAction` 三阶段可取消导航 | ✅ | ✅ | ✅ | 终点朝向对准有滑移问题（§6） |
| `MoveAction` 速度遥控（`/cmd_vel` 脉冲） | ✅ | ✅ | ✅ | 速度桥已按真机 bag 调过参 |
| 导览执行器（顺序到点 + 动作 + TTS 并发） | ✅ | ✅ | ✅ | `delta` 3 点 |
| 外部动作回放库（上传/执行/互锁/导览引用） | ✅ | ✅ dry-run | ⚠️ | `action_server.py` 真机执行**未见验证记录** |
| AeroMaze planner overlay（`G1_API_PLANNER=aero`） | — | ✅ | ⚠️ | 是脚本默认值，overlay 编译 + 实机行为**未见验证记录** |
| DDS 实连（`dds_backend=real`：LocoClient/AudioClient/ArmClient） | — | stub | ⚠️ | 见下面的「文档与现实的张力」 |
| `g1_replay_min` 双臂回放 | ✅ dry-run | — | ❌ | **从未在真机跑过**，README §8 明确说明 |

> ### ⚠️ 文档与现实有张力，接手时请自己核实一遍
> [g1_api/docs/TODO.md](g1_api/docs/TODO.md) 里「真机 DDS 实连未在硬件验证」等条目
> 写于 **2026-08-22**；但 08-23 和 08-31 都有真机 rosbag，08-31 还按真机数据调了
> 速度桥限幅/死区，09-01 现场建了图并配了导览。也就是说 **TODO 的部分条目已滞后于
> 实际进展，但没人回去更新**。
>
> 上表的「真机」列是我按现场产物（bag、地图、导览定义、脚本调参记录）推断的，
> **不是逐条实测过的**。请按 [§3](#3-接手第一天) 的冒烟清单自己过一遍，
> 顺手把 TODO.md 的状态改对。

---

## 3. 接手第一天

### 3.1 开发机（30 分钟，零风险）

```bash
# ① 建一个能跑的 Python 环境（本机 conda base 是 3.14，跑不了任何组件）
conda create -n g1_api_dev python=3.10 -y && conda activate g1_api_dev
pip install -e "/home/g1/workspace/project/concierge/G1/st_ws/g1_api[test]"

# ② 跑测试（README 声称 192 passed；这是你第一个要确认的数字）
cd /home/g1/workspace/project/concierge/G1/st_ws/g1_api && python3 -m pytest tests -q

# ③ 起 mock 服务，浏览器点一遍 /sdk
python3 -m g1_api        # → http://127.0.0.1:1448/sdk

# ④ 把现场地图导进 mock，在编辑器里看一眼导览点
#    地图库目录：g1_api_maps/（服务会读 map.map_storage_dir，默认 ~/.g1_api/maps）
G1_API_MAP_MAP_STORAGE_DIR=/home/g1/workspace/project/concierge/G1/st_ws/g1_api_maps \
  python3 -m g1_api      # → http://127.0.0.1:1448/sdk/map-editor
```

读文档的顺序：[system_bible.html](g1_api/docs/system_bible.html)（理解架构）→
[deploy_handbook.html](g1_api/docs/deploy_handbook.html)（部署与 SOP）→
[API.md](g1_api/docs/API.md)（端点语义）→ [TODO.md](g1_api/docs/TODO.md)（欠账）。

### 3.2 真机（半天，需要人在现场 + 急停在手）

按 [DEPLOY_PC2.md §7](g1_api/docs/DEPLOY_PC2.md) 的**分层测试计划**逐层走，
**别跳层**。每层只开当前要用的开关：

| 层 | 开的开关 | 测什么 | 风险 |
|---|---|---|---|
| 0 | 无 | 开发机 mock 测试全绿 | 无 |
| 1 | 无 | PC2 上 mock 模式起服务，`/healthz`、`/robot/info` 能通 | 无 |
| 2 | **全关** | real 模式读端点：`/capabilities`、`/action-factories`、`/robot/health` | 无 |
| 3 | `VOICE` + `ARM` | `GET /arm/v1/action-list`（**真机权威表**）、说一句 TTS | 低（有声/动手，不动腿） |
| 4 | 加 `NAVIGATION` + `MAP_WRITE` | 选图 → 重定位 → `RotateTo` 原地转一点 | 中（会动，手搭急停） |
| 5 | 加 `HAND` + `TOUR`（+ 可选 `MOTION`） | 短距 `MoveTo` → 3 点导览全程 | 高（全程有人 + 急停） |

冒烟命令：

```bash
curl -s http://127.0.0.1:1448/healthz
curl -s http://127.0.0.1:1448/api/core/system/v1/robot/health   # interlocks[] 是排障第一站
curl -s http://127.0.0.1:1448/api/core/motion/v1/action-factories
curl -s http://127.0.0.1:1448/api/core/arm/v1/action-list        # 真机 GetActionList() 权威表
```

---

## 4. 日常操作 SOP（真机）

现场怎么开机干活，权威版是 [g1_api/docs/quickstart.html](g1_api/docs/quickstart.html)
（浏览器直接打开）。这里只记骨架。

### 开机

```bash
ssh unitree@192.168.220.8          # 密码 123；或 sshpass -p 123 ssh ...
docker exec -it AbotClaw bash      # 每个终端都要单独 exec
```

四个终端，**必须按序**，每个都在容器内的**交互 shell** 里用 `source`（不要 `bash`，
`conda activate` 依赖交互 shell 初始化）：

| 终端 | 命令（省略 `/home/unitree/abotclaw_nv/g1_api/deploy/` 前缀） | 说明 |
|---|---|---|
| ① | `source robot_env.sh && roscore` | ROS master，**必须独立长驻** |
| ② | `source robot_velbridge.sh` | 速度桥。⚠️ 一起来机器人就会真的走 |
| ③ | `source robot_extexec.sh` | 外部动作执行器 :9100（导览要用回放动作才需要） |
| ④ | `source robot_g1api.sh` | g1_api → `http://192.168.220.8:1448/sdk` |

然后浏览器开 `/sdk`：**选图 → 重定位 → 干活**。选图和重定位是服务重启后的必经两步。

### 常用变体

```bash
G1_API_PLANNER=course source robot_g1api.sh   # 回课程导航栈（默认是 aero）
source robot_debugbridge.sh                   # 速度桥诊断版：逐秒打印 + 自动录 bag
G1_VEL_RECORD=1 source robot_velbridge.sh     # 安静版也录 bag
G1_DEBUG_NO_BAG=1 source robot_debugbridge.sh # 诊断但不录
./laptop_rviz.sh nav                          # 笔记本侧远程 RViz（mapping|nav）
```

拉 bag 回开发机分析：

```bash
mkdir -p /home/g1/workspace/project/concierge/G1/st_ws/from_robot   # 目录目前不存在，先建
sshpass -p 123 scp unitree@192.168.220.8:/home/unitree/abotclaw_nv/bags/*.bag \
  /home/g1/workspace/project/concierge/G1/st_ws/from_robot/
```

### 关机

Ctrl-C 逐个停终端（④→③→②→①）。停②之后 `velocity_bridge_down` 互锁会亮，
这是正常的。

---

## 5. 关键设计决策（别无意中推翻）

每条都有踩过的坑做背书。改之前先弄清为什么。

| 决策 | 为什么 |
|---|---|
| **默认 mock，真机适配器惰性导入** | 开发机没有 ROS，`import g1_api` 也必须不失败。有测试守护（`test_g1_import.py`） |
| **`ALLOW_*` 全部默认 false** | 拿到代码的人不可能意外让机器人动起来。有测试守护（`test_safety_gate.py`） |
| **全库锁 Python 3.8 语法** | 真机 ROS1 Noetic 是系统 py3.8。有测试守护（`test_py38_compat.py`） |
| **models/core/services 禁 import fastapi/pydantic** | wire 格式不能渗进领域层。有测试守护（`test_layering.py`） |
| **端口 1448，不动 8888** | 8888 被课程 `agent_server` 占用。g1_api **不依赖**它的 HTTP，`robot_sdk` 是复制进项目而非走它的接口 |
| **roscore 独立长驻，不靠 roslaunch 带起** | 否则 master 生命周期绑在 launch 上，`:select` 切图或建图 `:finish` 停栈时 master 一起死，已注册的 rospy 节点不会向新 master 重注册 |
| **自带参数化 `g1_api_navigation.launch`** | 课程 `navigation.launch` 把地图路径硬编码三处，`:select` 切图没法复用 |
| **launch 参数叫 `grid_yaml` 而非课程的 `2dmap_file`** | ROS 计算图名不能以数字开头，命令行 `2dmap_file:=...` 会被 roslaunch 解析成**话题重映射**，参数静默落回默认值（实测踩过）。launch 内部再转传给课程的 `2dmap_file`（XML 传参不受限） |
| **就绪判据用 `/slam_reloc` 服务 + `/move_base` 节点注册，不用 `/move_base/status`** | 该话题在重定位前不会出现——move_base 阻塞在 costmap 等 map TF |
| **健康检查走 rosgraph master API + TCP 验证** | 只查进程存活会被 master 的**陈旧注册**骗过 |
| **自带速度桥 `g1_control_vel.py`，不用课程版** | 课程死区过大（vx/vy 0.2、wz 0.3）：TEB 起步和近障碍的小指令被置零，0.1 s 就进锁定，表现为「目标在身后原地转不动」、窄道站桩。现版限幅 0.8/0.2/0.8、死区 0.1/0.1/0.15，`robot_velbridge.sh` 再把 wz 死区压到 0.08 抗蛇形 |
| **手部 IP 用 `127.0.0.1`** | 手 server 与 g1_api 同机（host 网络），回环最稳、换网段免改 |
| **DDS 网卡显式写 `eth0`** | 课程代码里 iface 默认值三处不一致（eth0/eno1/enp4s0） |
| **`aero` 是 planner 默认值** | 分层到点：位置由 `/move_base/result` 判，朝向由 g1_api 旋转阶段完成 |
| **不重打 Docker 镜像** | 项目根已被 `-v` 挂载，代码放进去即自动进容器；镜像 24 GB，重打不划算 |

---

## 6. 头号问题：终点旋转定位滑移

**现象**（2026-08-23 16:22 真机 bag 实测）：导航到点后原地旋转对准朝向时，
FAST-LIO 的 `map→local` 修正**每 0.1 s 跳 0.2 ~ 0.27 m**，4 秒内在零速度指令下
位姿漂移 **0.85 m**。`course` 和 `aero` 两套栈都受影响——两者的终点对准都是原地转。

**当前缓解**（治标）：
- 航迹点 yaw 顺着进入方向设，避免到点后 U 型转身
- yaw 容差已放宽到 0.25

**根治方向**（未做，专项）：查 FAST-LIO localize 在旋转时的退化行为，以及
reloc-check 的修正策略。这是接手后最值得投入的一件事。

**相关线索**：`bags/debug_0831_103851.bag`（08-31 调试桥录的实走数据，
含 `/cmd_vel /slam_odom /tf /scan`）。08-31 的速度桥调参就是拿这类 bag 做的。

---

## 7. 已知陷阱清单

源码考证过的，踩一次浪费半天。

**环境 / 部署**
- 课程 `README` 大面积过时；真实栈是 **ROS1 Noetic**，无 ROS2/Nav2，**禁止 `import rclpy`**
- 容器里 miniconda 的 python 排在 PATH 前面，`catkin_make` 会误抓它（报
  `Unable to find 'em'`）→ `robot_build_aero.sh` 已强制 `-DPYTHON_EXECUTABLE=/usr/bin/python3`
  并在检测到污染的 CMake 缓存时清 `build/`
- AeroMaze overlay 白名单**必须包含 `map_server`**：overlay 的 `ROS_PACKAGE_PATH` 会让
  fork 源码树遮蔽 apt 包，没编译的遮蔽包＝可执行文件缺失，`gridmap_load` 里的
  map_server 会**静默起不来**
- `robot_*.sh` 必须 `source` 不能 `bash`（`conda activate` 需要交互 shell 初始化）
- 改 `ALLOW_*` 或任何 `G1_API_*` 后**必须重启服务**

**建图 / 定位**
- 建图后 `mymap.yaml` 里的 `nan` **必须改成 `0`**，否则 map_server 报错（走 API 的
  `:finish` 已处理，手动流程要自己改）
- `:finish` 的顺序是有讲究的：先 map_saver 存 2D 栅格（趁 octomap 还活着）→ 再停建图栈
  （触发 pcd 自动保存，**并校验文件新鲜度**，防止拿到上一次的旧图）
- `robot_sdk/config.yaml` 硬编码 `192.168.123.199`，与教程的 `.222` 矛盾——注意改

**动作 / 硬件**
- 导航前机器人**必须处于走跑模式**（无 API 可查，纯文档性前置）
- TTS 前必须先 `LocoClient.Init()`（VoiceSDK `enable_loco=True`）
- 手臂 `action_id 31` 不在 SDK 表内——**不确定**，真机先 `GetActionList()` 校验
- G1 旋转机构物理限位约 **±1.3 rad**，朝向容差建议 ≥ 0.5 rad
- 到点判定**不依赖** `/move_base/result`（用 TF 距离 + feedback）；`aero` 模式例外，
  位置由 result 判，且 `PREEMPTED` 一律按失败上报（外部抢占，如 RViz 手发目标）
- **G1 的零位是「小臂向前平举」，不是自然下垂**——回放脚本已在发布线程启动前把
  `q_target` 设成实测关节角，改这段要小心
- `g1_replay_min` 只降 `--frequency` 不降 `--velocity-limit` 是错的：手臂会「瞬间冲到位
  → 干等 → 再冲」，一顿一顿地抽搐，比原速更危险

**网络**
- 两张网别搞混：`192.168.123.x` 内部网（DDS/雷达，网卡 `eth0`，**别改**）vs
  `192.168.220.x` 对外网（ssh/RViz/浏览器，`ROBOT_IP`）
- 多张网卡都在 `192.168.123` 网段时 CycloneDDS 自动选网卡会挑错（ping 通但 DDS 收不到）
  → `export UNITREE_NIC=<网卡名>`

---

## 8. 未完成事项

完整列表在 [g1_api/docs/TODO.md](g1_api/docs/TODO.md)（作者原则是「不藏」）。
这里按优先级摘要，并给出我的判断。

### P0 — 影响正确性/安全

| 事项 | 状态 |
|---|---|
| **终点旋转定位滑移** | 见 [§6](#6-头号问题终点旋转定位滑移)。**接手后第一优先** |
| 真机 DDS 实连路径（`dds_backend=real`）逐条核实 | `AudioClient` 的 `TtsMaker/GetVolume/SetVolume/LedControl` 返回值解析是按 `unitree_sdk2py` 惯例**推断**书写的，需真机确认 |
| `g1_replay_min` 真机首跑 | **从未在真机跑过**。首次务必：吊装 + 急停在手 + `--velocity-limit 2.0` 起步 |

### P1 — 语义补全

- 动作工厂 `options` schema 里 `MoveAction.duration_ms` 上限硬编码 3000，与
  `SafetyConfig.max_move_duration_ms` **双源**（服务层已按 config 二次校验，schema 描述是静态默认值）
- `RotateAction`/`RotateToAction` 的 `absolute` 字段是内部推导值，不在 wire schema
- 导览堵路处理（全局路径空 >1.5 s → TTS「请让一让我」10 s 限频 → `clear_costmaps`）
  在 mock 里只以注入的 `GOAL_UNREACHABLE` 演示，**真机路径监测未实现**
- 导览 `on_failure=retry_once` 的语义实现为「重试后仍失败 → 标记 failed 并中止」；
  若要「重试一次后 skip」需改实现并补测试
- AeroMaze 走廊层 `/path_boundry_path` 的发布器
  （`robot_settings/scripts/publish_interpolated_path.py`）**未接入 API**，需要走廊时得手动跑
- 编辑器 UI 没有外部动作 id 下拉，得手填 `type=replay` + `id`
- 外部动作执行是**同步阻塞**（时长 + 30 s 兜底超时）；查询串只能 ASCII（中文名会 400）

### P2 — 工程化

- **无鉴权**（对齐思岚 1448 端口的无鉴权设计）。注意 `G1_API_SERVER_HOST=0.0.0.0`
  之后，123 网段内**任何设备都能调 API 让机器人动**。要暴露到不可信网络必须加
  bearer token（`problems.py` 已预留 401/403 渲染）
- 无 OpenAPI 生成工具（`tools/` 只有 `md_serve.py` 和 `pack_g1map.py`）；FastAPI 自带
  `/openapi.json` 可用
- 地图包 `unpack` 对 `grid.yaml` 的 `image` 字段只做了 `nan→0`，没强制改写路径值；
  真机 map_server 报 image 路径错时需补强
- mock 导航是匀速直线插值，无 A*/TEB/代价地图仿真，**不做避障语义验证**
- **无持久化**：重启后内存态（当前选中地图、定位状态）丢失；mock 地图库落盘是 best-effort

### 明确不做

- 不依赖课程 `agent_server` 的 8888 HTTP
- 不实现思岚 24 个动作工厂全集，只做 4 个
- 不实现思岚 delivery / elevator / fleet 语义

---

## 9. 环境与凭据清单

| 项 | 值 |
|---|---|
| PC2 对外 IP | `192.168.220.8`（ssh `unitree` / 密码 `123`；脚本里 `ROBOT_IP` 默认值） |
| PC2 内部 IP | `192.168.123.164`（DDS、相机推流、灵巧手 TCP、`AGENT_SERVER_HOST`） |
| MID360 雷达 | `192.168.123.120`（`LIVOX_LIDAR_IP`；`LIVOX_HOST_IP=192.168.123.164`） |
| 容器 | `AbotClaw`，`--network=host --privileged`，项目根 `-v` 挂载 |
| conda env（真机） | `g1_agent` (3.10) 跑 g1_api；`linkerhand` (3.10) 跑灵巧手；ROS1 用系统 py3.8 |
| CycloneDDS | `<项目根>/unitree_sdk2_python/cyclonedds/install`（源码编译产物） |
| 端口 | 1448 g1_api / 11311 roscore / 9100 外部动作 / 5678 灵巧手 / 8765 相机 / ~~8888 课程~~ |
| 全局配置 | [config.env](config.env)，**换机器只改「机器配置」区** |
| 现场地图 | `g1_api_maps/delta`（分辨率 0.05，origin `[-20.25, -38.95, 0]`，3 个导览点，英文 TTS，站点是 Punggol Digital District） |

> 密码 `123` 是明文写在脚本注释和 SOP 里的（内网、课程默认）。若系统要上不可信
> 网络，凭据和 `SERVER_HOST=0.0.0.0` 都得先处理。

---

## 10. 风险与债务

按严重程度排序，接手后建议依次处理。

1. **没有版本控制。** 本工作空间**不是 git 仓库**（`navigate/WK` 下有个 `.github/`，
   但工作空间本身没初始化）。改动没有历史、没有 diff、没有回滚。唯一的兜底是
   `../st_ws_backup_2026-09-08_1419.tar.gz`（157 MB）。
   **第一件事就该 `git init`**，并把 `build/`、`devel/`、`__pycache__/`、`logs/`、
   `*.bag`、`*.pcd`、`.venv/` 加进 `.gitignore`（这些占了 400+ MB）。

2. **文档状态滞后于实际进展。** [TODO.md](g1_api/docs/TODO.md) 的验证状态停在
   08-22，之后的真机进展（08-31 调参、09-01 建图配导览）没回写。走完
   [§3.2](#32-真机半天需要人在现场--急停在手) 的分层冒烟后请把它更新。

3. **`quickstart.html` 有三份副本**，内容已分叉：
   [g1_api/docs/quickstart.html](g1_api/docs/quickstart.html) 是权威版；
   根目录的 `quickstart.html` 和 `../quickstart.html` 是早期手改副本。
   建议删掉副本或改成软链，只留一份。

4. **无鉴权 + `0.0.0.0` 监听**：123 网段内任何设备都能调 API 让机器人动。见 §8 P2。

5. **课程代码与自研代码交织**：`navigate/WK`（课程）、`robot_client`（课程）与
   `g1_api`/`g1_replay_min`（自研）在同一树里，vendor 目录（`unitree_sdk2_python`、
   `Livox-SDK2`、`hardware/linkhand/LinkerHand`）也混在其中。git 化时考虑用
   submodule 或至少在 README 的归属表里维持边界（[README.md §2](README.md#2-目录地图) 已列）。

6. **构建产物入库**：`navigate/*/build`、`devel` 都在树里（catkin 产物）。它们在真机
   挂载卷里持久化是有意的（overlay 只编一次），但不该进 git。

---

## 11. 交接检查清单

接手方逐项打勾：

- [ ] 读完 [README.md](README.md) 和本文
- [ ] 读完 [system_bible.html](g1_api/docs/system_bible.html)（架构）与
      [deploy_handbook.html](g1_api/docs/deploy_handbook.html)（部署 SOP）
- [ ] 开发机建好 3.10 环境，`pytest tests` 全绿，记下实际测试数
- [ ] mock 起服务，`/sdk` 点过一遍主要接口，`/sdk/map-editor` 能看到 `delta` 的 3 个点
- [ ] `git init` + `.gitignore`，首次提交
- [ ] 能 ssh 上 PC2、`docker exec` 进 `AbotClaw`、四个终端按序起来
- [ ] 真机分层冒烟层 0–3 走通（不动腿）
- [ ] 真机分层冒烟层 4–5 走通（现场有人 + 急停在手）
- [ ] 回写 [TODO.md](g1_api/docs/TODO.md) 的验证状态
- [ ] 处理 quickstart.html 三份副本的分叉
- [ ] 对 [§6 定位滑移](#6-头号问题终点旋转定位滑移) 有自己的判断和计划
