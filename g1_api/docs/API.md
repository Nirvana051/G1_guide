# g1_api API 参考

服务端口 **1448**。错误 body 用 RFC 9457 `application/problem+json`
（`{type,title,status,code,detail,trace_id,interlocks?}`）。

## 动作模型（思岚异步命令模式）

一切运动通过唯一入口提交；提交返回 `ActionInfo`，进度用轮询查询。

```http
POST /api/core/motion/v1/actions
{ "action_name": "g1.actions.MoveToAction", "options": { ... } }
→ 200 { "action_id": <int>, "action_name": "...", "stage": "...",
        "state": { "status": 0|1|3|4, "result": 0|-1|-2, "reason": "" } }
```

| 语义 | 值 |
|---|---|
| `status` | `0 NewBorn → 1 Working → (3 Paused) → 4 Done`；**没有 2** |
| `result` | `0 成功 / -1 失败 / -2 被取消`，**仅当 status==4 才有意义** |
| 空闲 | `GET /actions/:current` → **404**，body 为 JSON 字符串 `"Action Not Found"`（正常控制流） |
| 单槽位 | 有动作在跑时再提交 → **400 `Can not create action`**，body 附 `code:"ROBOT_BUSY"` |
| 取消 | `DELETE /actions/:current` → `status 4, result -2` |

运行期失败**不走 HTTP 错误**——永远 `status 4 / result -1 / failure_code / reason`。

### 动作工厂（四个）

| action_name | options | 底层（真机） |
|---|---|---|
| `g1.actions.MoveAction` | `{vx, vy, omega, duration_ms}` 速度遥控，**不避障**；duration 上限默认 ≤3000ms；死区 vx/vy 0.2、ω 0.3 | `LocoClient.Move` → `StopMove` |
| `g1.actions.MoveToAction` | `{target:{x,y}, yaw, with_yaw, reach_threshold, timeout_s}`；`with_yaw` **必填显式布尔** | `Nav2Anywhere.nav_to_with_yaw` |
| `g1.actions.RotateAction` | `{angle}` **相对**角度，弧度，CCW 正 | `rotate_to_yaw` |
| `g1.actions.RotateToAction` | `{yaw}` **绝对**朝向 | `rotate_to_yaw` |

`action_name` 兼容别名：`slamtec.agent.actions.MoveToAction` /
`MoveByAction` / `RotateAction` / `RotateToAction`。

`failure_code` 枚举：`GOAL_UNREACHABLE / TIMEOUT / CANCELLED_BY_USER /
LOCALIZATION_NOT_READY / SAFETY_INTERLOCK / UNKNOWN`（+ `PATH_BLOCKED` 等）。

## 地图包（`.g1map`）

`tar.gz`，内容：

```text
mymap.g1map
├── manifest.json    { map_id, name, created_utc, resolution, origin,
│                       tour_points:[{name,x,y,yaw,actions:[{type:"arm",id}|{type:"hand",cmd}],
│                                     tts_text,dwell_s}], checksums:{...} }
├── map.pcd / ground_map.pcd / grid.pgm / grid.yaml
```

| 方法 | 路径 | 语义 |
|---|---|---|
| GET | `/api/core/slam/v1/maps` | 列出地图库 |
| POST | `/api/core/slam/v1/maps` | 上传 `.g1map`（octet-stream），校验 checksums 入库 |
| GET | `/api/core/slam/v1/maps/{id}/package` | 下载 `.g1map` |
| GET | `/api/core/slam/v1/maps/:current` | 当前地图 manifest |
| POST | `/api/core/slam/v1/maps/{id}/:select` | 选图 → `{selected:true, requires_relocalization:true}` |
| DELETE | `/api/core/slam/v1/maps/{id}` | 删除（当前选中的 → 409） |

## 建图录制（mapping 会话）

把课程的手动建图流程做成 API 会话。**一次只允许一个会话**；会话独占 SLAM 栈：
`:start` 会先停掉导航栈（丢失当前定位），`:finish`/`:cancel` 停掉建图栈——任意
时刻只有一套 FAST-LIO 在跑，后台保持干净。建图期间导航/导览被 `mapping_running`
互锁拒绝，但 **MoveAction 速度遥控保持可用**（用于驱动建图走位；真机也可手柄遥控）。

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/core/slam/v1/mapping/:start` | 停导航栈 → 起建图栈（等 map_builder 存活，超时 60s 报错） |
| GET | `/api/core/slam/v1/mapping` | `{active, started_utc, elapsed_s, note}`；launch 崩溃在 note 报出 |
| POST | `/api/core/slam/v1/mapping/:finish` | body `{map_id?, name?}`。顺序：map_saver 存 2D 栅格（趁 octomap 还活着）→ 停建图栈（触发 map.pcd/ground_map.pcd 自动保存，**校验文件新鲜度**防止拿到上一次的旧图）→ 打包 `.g1map` 注册入库。之后 `:select` + 重定位即可导航 |
| POST | `/api/core/slam/v1/mapping/:cancel` | 停栈、不保存 |

前置配置（real 模式）：`mode.mapping_launch_file`（建图 launch，仓库自带
`deploy/g1_api_mapping.launch`）、`map.mapping_output_dir`（map_builder 的
写死输出目录，默认 `/home/unitree/abotclaw_nv/navigate/map`）。mock 模式开箱可用。

## 定位与重定位

| 方法 | 路径 | 语义 |
|---|---|---|
| GET | `/api/core/slam/v1/localization/pose` | `{x,y,z,yaw,pitch,roll}`（TF map→body） |
| POST | `/api/core/slam/v1/localization/:relocalize` | body `{x,y,yaw}` → `/slam_reloc`（用当前地图 map.pcd），清 `localization_not_initialized` |
| GET | `/api/core/slam/v1/localization/status` | `{initialized, map_id, note}`（**无 0-100 质量分**，FAST-LIO 没有） |

## 系统与健康

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/api/core/system/v1/robot/info` | 厂商/型号(Unitree G1)/软件版本/device_id/mode |
| GET | `/api/core/system/v1/capabilities` | 子系统就绪标志 |
| GET | `/api/core/system/v1/robot/health` | `{hasWarning,hasError,hasFatal,baseError:[...],interlocks:[...]}` |

## TTS / 手臂 / 灵巧手（不占动作槽位）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/core/voice/v1/tts` | `{text, speaker_id=0, wait=false}` → `{accepted, est_duration_s}`（时长 `len(text)*0.195` 秒估算） |
| GET/PUT | `/api/core/voice/v1/volume` | 0–100 |
| PUT | `/api/core/voice/v1/led` | `{r,g,b}` 0–255 |
| GET | `/api/core/arm/v1/action-list` | 手臂动作表（权威来源） |
| POST | `/api/core/arm/v1/actions` | `{action_id}` → 执行后自动 `ExecuteAction(99)` 复位 + 等 3s |
| POST | `/api/core/hand/v1/command` | `{cmd:"1".."19"}` → 灵巧手 TCP 5678 |

## 导览应用

| 方法 | 路径 | 语义 |
|---|---|---|
| GET/PUT | `/api/tour/v1/tours` | 当前地图的导览定义（点回写 manifest） |
| POST | `/api/tour/v1/tours/:start` | 前置检查：地图已选、已重定位、无致命健康、`ALLOW_NAVIGATION=true` |
| POST | `/api/tour/v1/tours/:pause` / `:resume` / `:stop` | 控制 |
| GET | `/api/tour/v1/tours/:current` | 整体状态 + 当前点 + 每点结果 |
| GET | `/api/tour/v1/events` | 导览事件流（`from_cursor` 续读） |

## 错误语义

- `400 VALIDATION_ERROR`：请求本身坏。
- `409 ROBOT_BUSY / SAFETY_INTERLOCK`：请求合法但当前状态不行。
- `503 NOT_READY`：子系统未就绪。

保留的思岚怪癖只有两个：`404-即-空闲`（`actions/:current`）与动作提交的
`400 "Can not create action"` 文案（附机器可读 `code`）。
