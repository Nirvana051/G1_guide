# g1_api 真机部署 runbook（PC2）

> **本文件已让位于图文版上机手册**（部署三目录+overlay 编译、五终端 SOP、
> aero planner、外部动作回放库、互锁对照、Day-1 剧本、远程 RViz、地图编辑器）：
> 本地镜像 [`docs/deploy_handbook.html`](deploy_handbook.html)（浏览器直接打开），
> 在线版 https://claude.ai/code/artifact/0207d8a3-8970-4643-bb0f-5debe7cee02a ；
> 快速上手（今天怎么开机干活）另见 [`docs/quickstart.html`](quickstart.html)。
> 手册 01 节即"全新机器从零部署"，与真机目录树逐项核对过
> （镜像备份：开发机 `/home/g1/workspace/project/concierge/G1/st_ws/from_robot/abotclaw_nv/`）。
> 下文保留作纯文本备查，个别细节可能滞后，以手册为准。

> ⚠️ 本文由人类操作员在真机上执行。开发机全程用 mock，不碰真机。
> 所有 IP 与路径以课程 `README_G1_Deploy.md` 为准。

## 0. 硬事实

| 项 | 值 |
|---|---|
| 机器人 PC2 | `192.168.123.164`（unitree/123） |
| 容器 | `AbotClaw`，`--network=host --privileged` |
| 项目根 | `/home/unitree/abotclaw_nv` |
| conda env | `g1_agent`（3.10）；ROS1 系统 py3.8 |
| 服务端口 | **1448**（8888 已被课程 agent_server 占用，勿用勿依赖） |
| DDS 网卡 | `G1_NETWORK_INTERFACE=eth0`（**显式配置，不给默认**） |

## 1. 前置进程清单（冒烟顺序）

0. **roscore**：显式起一个长驻 master（环境前置块后直接 `roscore`）。不要依赖
   roslaunch 自动带起——那样 master 生命周期绑在该 launch 上，`:select` 切图或
   `/mapping/:finish` 停栈时 master 一起死，g1_api 内已注册的 rospy 节点不会向新
   master 重注册。roscore 默认监听所有网卡，笔记本 RViz 天生可达 11311。
1. **走跑模式**：导航前机器人必须处于走跑模式（无已知 API 可查，作为文档性前置）。
2. **导航 launch**（g1_api 自管，见下）。
3. **速度桥**：`python3 unitree_sdk2_python/example/g1/high_level/g1_control_vel.py eth0`
   —— `/cmd_vel` 桥，50Hz，限幅 0.5/0.2/0.7。**导航能动的前提是它活着**。
4. **灵巧手 server**：`./hardware/scripts/start_hand_server.sh`（TCP 5678）。

## 2. 安装与启动 g1_api

```bash
docker exec -it AbotClaw bash
cd /home/unitree/abotclaw_nv
git clone <your g1_api checkout> g1_api   # 或 scp 上传

# 组装 Python 环境（conda g1_agent + ROS1 系统 py3.8）
conda activate g1_agent
source /opt/ros/noetic/setup.bash
cd /home/unitree/abotclaw_nv/navigate/WK/G1Nav2D
source devel/setup.bash
cd /home/unitree/abotclaw_nv/g1_api

# 配置
export G1_API_MODE=real
export G1_API_MODE_NETWORK_INTERFACE=eth0    # mode 段的 network_interface 字段
export G1_API_SERVER_HOST=0.0.0.0            # 默认 127.0.0.1 只能本机 curl；改 0.0.0.0
                                             # 后笔记本可直接开 /sdk 和 /sdk/map-editor
                                             # （容器是 --network=host，docker run 无需改动；
                                             #   注意 123 网段内任何设备此后都能调 API）
export G1_API_MODE_LAUNCH_FILE=/home/unitree/abotclaw_nv/g1_api/deploy/g1_api_navigation.launch
export G1_API_MODE_MAPPING_LAUNCH_FILE=/home/unitree/abotclaw_nv/g1_api/deploy/g1_api_mapping.launch
                                             # 不配 mapping_launch_file 则 /mapping/:start 返回
                                             # "mapping via API is unavailable"（建图输出目录
                                             # 默认已是 navigate/map，无需配）
export G1_API_HAND_ROBOT_IP=192.168.123.164
export G1_API_SAFETY_ALLOW_NAVIGATION=true
export G1_API_SAFETY_ALLOW_MAP_WRITE=true
export G1_API_SAFETY_ALLOW_ARM=true
export G1_API_SAFETY_ALLOW_HAND=true
export G1_API_SAFETY_ALLOW_VOICE=true
export G1_API_SAFETY_ALLOW_TOUR=true
# allow_motion 仅在有持死键的人类在场时打开

python3 -m g1_api
```

> 环境变量名是 `G1_API_<SECTION>_<FIELD>`：`G1_API_MODE_NETWORK_INTERFACE`（mode 段）
> 而非 `G1_API_NETWORK_INTERFACE`；`G1_API_MODE` 是它的便捷别名。请用
> `python3 -c "from g1_api.config import load_config; print(load_config(warn=False).to_dict())"`
> 核对。

### 2.1 Docker 集成方式（推荐：复用现有容器，不重打镜像）

**结论：不要 merge 进课程代码/镜像；在现有 `AbotClaw` 容器里作为独立进程跑。**

g1_api 必须和导航栈处在**同一环境**——它要连同一个 ROS master（
`http://192.168.123.164:11311`）、同一条 DDS 网卡、同一个 `/cmd_vel` 速度桥、以及
`g1_api_navigation.launch` 里的 fastlio/move_base 工作区。这些全都在 `AbotClaw`
容器里，所以 g1_api **必须跑在那个容器内**（或一个 `--network=host` 且挂载同一
workspace 的独立容器，但没必要）。

最省事、也最工业的做法：

1. **代码挂载，不烧进镜像**：项目根 `/home/unitree/abotclaw_nv` 已被容器
   `-v` 挂载，把 `g1_api/` 放进该目录即自动进容器；不需要重打 24GB 的镜像。
2. **复用 `g1_agent` conda env**：课程 `agent_server`（8888）本身是 FastAPI，
   该 env 已带 `fastapi/pydantic/uvicorn/pyyaml/python-multipart/rospkg`，
   g1_api 的 Web 依赖已满足，无需额外装。
3. **独立进程、独立端口**：g1_api 用 **1448**，与 agent_server(8888)、导航
   launch、速度桥互不干扰；不 merge 进程、不 merge 代码 repo。

若将来要"开箱即用"再考虑做一层薄镜像（`FROM abotclaw_g1_final` + 拷入
`g1_api/` + `pip install -e .`），那是可选的交付优化，不是部署前提。

## 3. 参数化导航 launch（绕开课程硬编码地图路径）

课程 `navigation.launch` 把地图路径硬编码三处（slam_reloc 的 `pcd_path`、
`downsample_pointcloud` 的 `ground_map.pcd`、`gridmap_load` 的 `2dmap_file`）。
`deploy/g1_api_navigation.launch` 是参数化副本，`:select` 切图时用选中地图的
绝对路径作为 args 重启它：

```bash
roslaunch /home/unitree/abotclaw_nv/g1_api/deploy/g1_api_navigation.launch \
    pcd_path:=/home/unitree/abotclaw_nv/navigate/map/map.pcd \
    ground_pcd_path:=/home/unitree/abotclaw_nv/navigate/map/ground_map.pcd \
    grid_yaml:=/home/unitree/abotclaw_nv/navigate/map/mymap.yaml
```

> 注意：本层参数叫 `grid_yaml` 而不是课程的 `2dmap_file`——ROS 计算图名不能以
> 数字开头，命令行上 `2dmap_file:=...` 会被 roslaunch 解析成**话题重映射**而非
> launch 参数，参数静默落回默认值（实测踩过）。launch 内部会把它转传给课程
> `gridmap_load.launch` 的 `2dmap_file`（XML 传参不受此限制）。

## 4. 重定位与建图提醒

- 建图后 `mymap.yaml` 里的 `nan` **必须改为 `0`**（否则 map_server 报错）。
- 初始重定位：`rosservice call /slam_reloc "{pcd_path:'...map.pcd', x:0,y:0,z:0,roll:0,pitch:0,yaw:0}"`。
- g1_api 的 `POST /localization/:relocalize` 封装了这一步（用当前选中地图的 map.pcd）。
- `robot_sdk/config.yaml` 硬编码 `192.168.123.199`，与教程 `.222` 矛盾——注意改。

## 5. 冒烟清单

```bash
curl -s http://127.0.0.1:1448/healthz
curl -s http://127.0.0.1:1448/api/core/system/v1/robot/health
curl -s http://127.0.0.1:1448/api/core/motion/v1/action-factories
curl -s http://127.0.0.1:1448/api/core/arm/v1/action-list   # 真机 GetActionList() 权威表
# 完整导览：上传 .g1map → :select → :relocalize → /api/tour/v1/tours/:start
```

## 6. 安全开关与命名互锁对照表

所有 `ALLOW_*` 默认 **false**。关着时，对应接口返回 `409 SAFETY_INTERLOCK`，
`details.active_interlocks` 里给的就是下面的**互锁名**，`required_config_flag`
给的是要开的开关。开法：环境变量 `G1_API_SAFETY_<FLAG>=true`（大写），或写
`config/default.yaml` 的 `safety:` 段。**改完必须重启服务才生效。**

| 互锁名 | 要开的开关（环境变量） | 被拦截的接口 |
|---|---|---|
| `map_write_not_allowed` | `G1_API_SAFETY_ALLOW_MAP_WRITE=true` | `POST /slam/v1/maps`（上传）、`/maps/{id}/:select`（选图）、`DELETE /maps/{id}`（删图）、`PUT /tour/v1/tours`（回写导览点） |
| `navigation_not_allowed` | `G1_API_SAFETY_ALLOW_NAVIGATION=true` | `MoveToAction` / `RotateAction` / `RotateToAction`、`POST /tour/v1/tours/:start` |
| `motion_not_allowed` | `G1_API_SAFETY_ALLOW_MOTION=true` | `MoveAction`（速度遥控，**不避障**，最危险） |
| `voice_not_allowed` | `G1_API_SAFETY_ALLOW_VOICE=true` | `POST /voice/v1/tts`、`PUT /voice/v1/volume`、`PUT /voice/v1/led` |
| `arm_not_allowed` | `G1_API_SAFETY_ALLOW_ARM=true` | `POST /arm/v1/actions` |
| `hand_not_allowed` | `G1_API_SAFETY_ALLOW_HAND=true` | `POST /hand/v1/command` |
| `tour_not_allowed` | `G1_API_SAFETY_ALLOW_TOUR=true` | 导览 `:start`/`:pause`/`:resume`/`:stop` |

**硬件/状态互锁**（不是开关，是环境没满足；解除方式见右列）：

| 互锁名 | 含义 | 解除方式 |
|---|---|---|
| `map_not_selected` | 还没选地图 | 先 `POST /maps/{id}/:select` |
| `localization_not_initialized` | 选图后没重定位 | 先 `POST /localization/:relocalize` |
| `roscore_down` | roscore 没起 | 起 ROS master |
| `move_base_down` | move_base 没起 | 确认 `g1_api_navigation.launch` 已启动 |
| `velocity_bridge_down` | `/cmd_vel` 无订阅者 | 起 `g1_control_vel.py eth0` |
| `dds_unreachable` | DDS 通道不可达 | 确认 `G1_API_MODE_NETWORK_INTERFACE` 正确 |
| `tour_running` | 导览进行中 | 等结束，或 `POST /tour/v1/tours/:stop` |

## 7. 分层测试计划（从零风险到会动，逐层过，别跳）

| 层 | 模式 | 开的开关 | 测什么 | 风险 |
|---|---|---|---|---|
| 0 | mock（开发机） | 无 | `python3 -m pytest tests` 全绿 | 无 |
| 1 | mock（PC2） | 无 | 读端点 `/healthz`、`/robot/info` 能通 | 无（验证环境装对） |
| 2 | real（PC2） | **全关** | 读 `/robot/info`、`/capabilities`、`/action-factories`、`/robot/health` | 无 |
| 3 | real（PC2） | `ALLOW_VOICE` + `ALLOW_ARM` | `GET /arm/v1/action-list`（确认权威表）、说一句 TTS | 低（有声/动手，不动腿） |
| 4 | real（PC2） | 加 `ALLOW_NAVIGATION` + `ALLOW_MAP_WRITE` | 选图→重定位→`RotateTo` 原地转一点 | 中（会动；人在旁、手搭急停） |
| 5 | real（PC2） | 加 `ALLOW_HAND` + `ALLOW_TOUR`（+ 可选 `ALLOW_MOTION`） | 短距离 `MoveTo` → 3 点导览全程 | 高（全程有人 + 急停） |

原则：**每次只开当前这层要用的那个开关，别一次性全开**；`ALLOW_MOTION` 只在
有持死键/急停的人在场时才开。每层先用 curl 确认响应符合预期，再进下一层。

## 8. 已知陷阱（源码考证过）

- `README` 大面积过时；无 ROS2/Nav2/rclpy（真实栈 ROS1 Noetic），**禁止 import rclpy**。
- iface 默认值三处不一致（eth0/eno1/enp4s0）——一律显式配置。
- TTS 前必须先 `LocoClient.Init()`（VoiceSDK `enable_loco=True`）。
- 到点判定不依赖 `/move_base/result`（用 TF 距离 + feedback）。
- 手臂 `action_id 31` 不在 SDK 表内——**UNCERTAIN**，真机先 `GetActionList()` 校验。
- G1 旋转机构物理限位约 ±1.3 rad，朝向容差建议 ≥0.5 rad。
