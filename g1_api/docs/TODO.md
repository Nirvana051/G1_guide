# TODO / 未完成事项（不藏）

## P0 — 影响正确性/安全

- [x] ~~真机适配器仅做到"导入干净 + 打桩单测"~~ **已实现并在 Gazebo 仿真中
  端到端验证**（2026-08-22）：`adapters/g1/` 现已接通 地图库+`:select` 切图
  （launch 重启 + 就绪等待）、`/slam_reloc` 重定位（含 `/slam_reloc_check`
  轮询）、可取消三阶段 MoveTo/Rotate（`ros_bridge.nav_to_cancellable`）、
  cmd_vel 脉冲 MoveAction、TTS/手臂（DDS 层可选打桩：`mode.dds_backend=stub`）。
  仿真栈见 `/home/g1/workspace/project/concierge/G1/st_ws/g1_sim/`。**真机 DDS 路径（LocoClient/AudioClient/
  ArmClient 实连）仍未在硬件上验证**——仿真只验证了 ROS 侧。
- [x] ~~launch_manager.healthy() 退化为进程存活检查~~ 健康检查现走 rosgraph
  master API（roscore 可达、/move_base 节点注册 + TCP 存活、/cmd_vel 订阅者），
  并对 master 的**陈旧注册**做 TCP 连接验证。
- [x] ~~:select 未接 roslaunch 生命周期~~ 已接通并验证：停旧 launch（含孤儿
  进程清理）→ 以选中地图绝对路径重启 → 等 `/slam_reloc` 服务 + `/move_base`
  注册（注意：`/move_base/status` 话题在重定位前不会出现——move_base 阻塞在
  costmap 等 map TF，就绪判据不能用它）。
- [ ] 真机 DDS 实连路径（`dds_backend=real`）未在硬件验证；`AudioClient` 的
  `TtsMaker/GetVolume/SetVolume/LedControl` 返回值解析按 unitree_sdk2py 惯例
  书写，需真机确认。

## P1 — 语义补全

- [ ] 动作工厂的 `options` JSON Schema 中 `MoveAction.duration_ms` 上限硬编码 3000，
  与 `SafetyConfig.max_move_duration_ms` 双源；服务层已按 config 二次校验，schema
  描述仍是静态默认值。
- [ ] `RotateAction`/`RotateToAction` 的 options 校验里，`absolute` 字段是内部推导值，
  未出现在 wire schema（`rotate` 用 `angle`、`rotate_to` 用 `yaw`），已文档化。
- [ ] 导览的堵路处理（全局路径空 >1.5s → TTS"请让一让我" 10s 限频 → clear_costmaps）
  在 mock 中仅以注入的 `GOAL_UNREACHABLE` 失败语义演示，未实现真机路径监测。
- [ ] 导览 `on_failure=retry_once` 的"重试后仍失败"语义实现为"标记 failed 并中止"；
  若需"重试一次后 skip"请在此调整并补测试。
- [ ] AeroMaze planner（`G1_API_PLANNER=aero`）已在仿真验证；真机 overlay 编译
  （`deploy/robot_build_aero.sh`）与实机行为未验证。planner 反馈模式
  （`mode.nav_feedback=planner`）消费 `/move_base/result`，PREEMPTED 一律按失败上报
  （外部抢占，如 RViz 手发目标）；走廊层 `/path_boundry_path` 的发布器
  （`robot_settings/scripts/publish_interpolated_path.py`）未接入 API，需要走廊时手动跑。
- [ ] 外部动作（回放库）已在仿真 dry-run 验证（上传/执行/互锁/导览引用）；真机
  `action_server.py`（子进程跑 replay_min --motion）未在硬件验证。已知边界：
  执行是同步阻塞（时长+30s 兜底超时）；查询串只能 ASCII（中文名会 400）；
  编辑器 UI 尚无外部动作 id 下拉（手填 type=replay + id）。

- [ ] 真机 bag(08-23 16:22)实测：终点**原地旋转对准时定位滑移**——map->local 修正
  每 0.1s 跳 0.2~0.27m，4 秒内零速度指令下位姿漂移 0.85m。course/aero 都受影响
  （终点对准都是原地转）。缓解：航迹点 yaw 顺着进入方向设（避免到点 U 型转身）、
  yaw 容差已放宽到 0.25；根治需查 FAST-LIO localize 的旋转退化/reloc-check 修正
  策略，专项待做。

## P2 — 工程化

- [ ] 无鉴权（对齐思岚无鉴权的端口 1448）。如需暴露到不可信网络，应加 bearer token
  与鉴权中间件（当前 `problems.py` 已预留 401/403 渲染）。
- [ ] 无 OpenAPI 生成工具（`tools/` 为空）；FastAPI 自带 `/openapi.json` 可用。
- [ ] 地图包 `unpack` 对 `grid.yaml` 的 `image` 字段做了"改相对名"的注释说明，但未强制
  改写 image 字段值（仅做 `nan→0`）。若真机 map_server 报 image 路径错，需补强。
- [ ] mock 导航是匀速直线插值，无 A*/TEB/代价地图仿真；不做避障语义验证。
- [ ] 无持久化：mock 地图库落盘到 `map_storage_dir` 是 best-effort，重启后内存态
  （当前选中、定位状态）丢失。

## 未做（明确不做）

- 不依赖课程 agent_server 的 8888 HTTP；`robot_sdk` 复用方式是复制进项目，不是走其 HTTP。
- 不实现思岚的 24 个动作工厂全集，只做 build prompt 要求的四个。
- 不实现思岚 delivery/elevator/fleet 语义。
