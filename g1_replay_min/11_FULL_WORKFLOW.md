# 11_FULL_WORKFLOW.md（从 0 到 1 完整流程）

> 日期：2026-08-22
> 本机环境：主机 `rfouyang-kubuntu`，通过 USB 网卡 `enx00e04c3604a8`（IP **192.168.123.222/24**）连接 G1
> 关联：10_REPLAY_TUTORIAL.md（回放细节）、09_REAL_DEPLOY.md（真机安全手册）、07_RUN_COMMANDS.md（旧版启动手册）
> 标 `[已验证]` = 本机实跑过；`[未验证]` = 从未执行

---

## 全景

```
① 机器人 PC2 起图像服务  →  ② 主机起遥操作  →  ③ PICO 4 连网页  →  ④ 按 r/s 采数据
                                                                          ↓
                              ⑦ 真机回放  ←  ⑥ LeRobot 转换  ←  ⑤ 数据落盘
                                    ↑
                              （可选）Isaac Sim 里先验证
```

**关键 IP 速查**

| 设备 | IP | 作用 |
|---|---|---|
| 主机（你的电脑） | **192.168.123.222** | 跑遥操作、Vuer 网页服务（8012） |
| G1 PC1 | 192.168.123.161 | 运控主控，订阅 `rt/lowcmd` 驱动电机 |
| G1 PC2 | 192.168.123.164 | 图像服务（60000 / 60001） |

> ⚠️ 主机 IP 可能变。每次开工先 `ip -br addr | grep 192.168.123` 确认，PICO 网址要用这个 IP。

---

## ① 机器人 PC2：启动图像服务

```bash
ssh unitree@192.168.123.164
conda activate teleimager
teleimager-server
```

`[事实]` teleimager 带 `setup_autostart.sh`，会装 systemd 服务 `/etc/systemd/system/teleimager.service`。
如果已配开机自启，这步可跳过 —— 用下面这条确认：

```bash
# 在主机上执行，不用 ssh
ss -ltn | grep 60000        # 有输出 = 图像服务已在跑
```

### SSH 连不上？
若报 `REMOTE HOST IDENTIFICATION HAS CHANGED` —— 说明该 IP 后面的机器换过了（重装系统 / 换机器人）。
**确认确实换过之后**再执行：

```bash
ssh-keygen -f ~/.ssh/known_hosts -R 192.168.123.164
```

---

## ② 主机：启动遥操作

```bash
conda activate tv
cd ~/xr_teleoperate/teleop          # ← 必须进这一层！

python teleop_hand_and_arm.py \
  --arm G1_29 \
  --ee dex3 \
  --record \
  --task-name "real pick cube" \
  --img-server-ip 192.168.123.164 \
  --network-interface enx00e04c3604a8
```

### ⚠️ 三个必错的坑

**坑 1：必须 `cd teleop`。**
`[事实]` `robot_arm_ik.py:29` 用相对路径 `'../assets/g1/g1_body29_hand14.urdf'`。
在仓库根目录跑会解析成 `~/assets/`，报 `does not contain a valid URDF model` ——
**看起来像缺文件，其实是工作目录不对**。

**坑 2：`--ee dex3` 不能漏。**
`[事实]` episode_0010 就是漏了它，导致 `left_ee.qpos` / `right_ee.qpos` 全是空数组，
录到的数据只有手臂没有手，**事后补不回来**。

**坑 3：`--network-interface` 要填当前实际网卡。**
本机是 USB 网卡 `enx00e04c3604a8`；板载 `enp130s0` 现在是 DOWN。
填错会报 `does not match an available interface` + `channel factory init error`。

### 参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| `--arm` | `G1_29` | 机型：G1_29 / G1_23 / H1_2 / H1 / H2 / R1_A5 / R1_A7 |
| `--ee` | 无 | 末端：dex1 / dex1_internal / **dex3** / inspire_ftp / inspire_dfx / brainco |
| `--input-mode` | `hand` | `hand` 手势 / `controller` 手柄 |
| `--display-mode` | `immersive` | immersive / ego / pass-through |
| `--record` | 关 | **开启录制模式**，不加则只能遥操作不能存数据 |
| `--task-name` | `pick cube` | 数据子目录名。**建议真机数据单独命名**，别和仿真混 |
| `--task-dir` | `./utils/data/` | 数据根目录（相对 `teleop/`） |
| `--task-goal` | `pick up cube.` | 写进 data.json 的任务描述 |
| `--frequency` | `30.0` | 控制与录制频率 |
| `--img-server-ip` | — | 图像服务 IP（PC2）。**仿真时填本机 IP** |
| `--network-interface` | — | DDS 用的网卡名 |
| `--sim` | 关 | 发仿真（DDS domain 1）；不加就是真机（domain 0） |
| `--motion` | 关 | 运控模式，走 `rt/arm_sdk`。**回放采集一般不加** |

启动成功的标志：

```
Received camera config from server 192.168.123.164:60000
Serving file:///<你的路径>/xr_teleoperate at /static
Enter debug mode: Success                      ← DDS 到 PC1 通了
[G1_29_ArmIK] >>> Loading URDF (slow)...
```

---

## ③ PICO 4：连接网页

1. 戴上头显，连**同一个** WiFi / 网段
2. **（可选）先验图像服务**：浏览器打开 `https://192.168.123.164:60001`
   → `Advanced` → `Proceed to ip (unsafe)` → 点左上角 `start`，能看到头部相机画面即正常。
   这步同时完成对 WebRTC 自签证书的手动信任，**同一设备只需做一次**。
3. **打开遥操作网页**：

   ```
   https://192.168.123.222:8012/?ws=wss://192.168.123.222:8012
   ```

   > **这个 IP 是主机 IP，不是机器人 IP。** 换网卡 / 重新分配 IP 后必须跟着改。

   同样会弹证书警告 → `Advanced` → `Proceed to ip (unsafe)`
4. 进入 Vuer 页面后点 **`Virtual Reality`** 按钮，允许所有弹窗，进入 VR 会话

### 连不上排查

```bash
sudo ufw allow 8012                    # 防火墙放行
ip -br addr | grep 192.168.123         # 确认主机 IP 没变
```

---

## ④ 采数据：r / s / q 三个键

`[事实]` 定义在 `teleop_hand_and_arm.py:53-63`：

| 键 | 变量 | 作用 |
|---|---|---|
| **`r`** | `START = True` | **开始跟踪** —— 机器人开始跟随你的手。按之前机器人不动 |
| **`s`** | `RECORD_TOGGLE = True` | **录制开关**，按一次开始录、再按一次保存。**只在按过 `r` 之后有效** |
| **`q`** | `START=False, STOP=True` | **退出程序** |

> 按键要在**运行遥操作的那个终端窗口**里按（不是在 PICO 里）。

### 一条完整的采集流程

```
1. 终端启动成功，PICO 进入 VR 会话
2. 摆好起始姿势
3. 按 r          → 机器人开始跟手（此时还没录）
4. 按 s          → 终端打印 "New episode created: .../episode_0000"
5. 做完整个动作
6. 按 s          → 终端打印 "Episode saved successfully"
7. 重复 4~6 采下一条，编号自动 +1
8. 全部采完按 q  → 双臂自动回零位后退出
```

`[事实]` `--record` 没加的话，按 `s` 不会有任何反应（`if args.record and RECORD_TOGGLE`）。
`[事实]` 仿真模式下每次 `s` 结束还会自动 `publish_reset_category(1)` 复位物体。

---

## ⑤ 数据存在哪

```
~/xr_teleoperate/teleop/utils/data/<task-name>/episode_XXXX/
    ├── colors/      jpg 图像（路数取决于机器人的相机配置）
    ├── depths/
    ├── audios/
    └── data.json    ← 状态与动作，回放只用这个
```

编号从已有的最大值往后顺延，**不会覆盖**。

### 真机与仿真数据的差异（影响后续转换）

| | 仿真数据 | 真机数据（本机器人） |
|---|---|---|
| `sim_state` | 有 | **无** |
| 相机路数 | 3（`color_0/1/2`） | **1**（只有 `color_0`） |
| `*_ee.qpos` | 7 / 7 | 取决于是否加了 `--ee dex3` |

`[事实]` 相机路数由 PC2 推来的 `cam_config_client.yaml` 决定。本机器人当前：
`head_camera.enable_zmq=true, binocular=false`，双腕 `enable_zmq=false` → 每帧只有 1 张图。

---

## ⑥ 在 Isaac Sim 里先验证（可选但推荐）

**终端 1**（`unitree_sim_env`）：

```bash
conda activate unitree_sim_env
cd ~/unitree_sim_isaaclab

python sim_main.py --device cuda:0 --enable_cameras \
  --rendering_mode performance --render_interval 8 --camera_write_interval 8 \
  --clear_obstacles \
  --task Isaac-PickPlace-Cylinder-G129-Dex3-Joint \
  --enable_dex3_dds --robot_type g129
```

等 `ss -ltn | grep 60000` 有输出再继续。

**终端 2**（`tv`）：

```bash
conda activate tv
cd ~/xr_teleoperate

# 先看报告（离线，不发指令）
python teleop/replay_episode.py \
  --file "teleop/utils/data/real pick cube/episode_0000/data.json" --dry-run

# 发给仿真
python teleop/replay_episode.py \
  --file "teleop/utils/data/real pick cube/episode_0000/data.json" \
  --sim --wait-track 0.15 --wait-timeout 2 --go-home
```

`[结论]` `--wait-track` 在仿真里是必需的：仿真实时因子约 **1/42**（PROBLEM-011），
按墙上时钟发指令手臂根本追不上。真机不需要这个参数。

---

## ⑦ LeRobot 转换（只有要训练策略才需要）

> 只想让机器人把动作跑一遍 → **跳过本节**，直接看 ⑧。

### 选对 robot_type —— 这是最容易错的一步

| robot_type | motors | 相机 | 适用 |
|---|---|---|---|
| `Unitree_G1_Dex3` | 28 | 4（双目头+双腕） | 官方真机数据 |
| `Unitree_G1_Dex3_Sim` | 28 | 3（单目头+双腕） | **仿真采集**（CHANGE-20260820-001） |
| `Unitree_G1_ArmOnly_HeadCam` | 14 | 1（单目头） | **本机器人真机、无 ee**（CHANGE-20260822-001） |

选错的后果：相机会被错标（左腕当成右高清），或维度对不上直接报错。

### 命令 `[已验证]`

```bash
# 1) 搭目录 —— --raw-dir 指向【任务目录的父目录】，episode 必须叫 episode_0000
rm -rf ~/datasets_ep0010
mkdir -p ~/datasets_ep0010/"real pick cube"
cp -r ~/"xr_teleoperate/teleop/utils/data/pick cube/episode_0010" \
      ~/datasets_ep0010/"real pick cube"/episode_0000

# 2) 转换
conda activate unitree_lerobot
cd ~/unitree_lerobot

python unitree_lerobot/utils/convert_unitree_json_to_lerobot.py \
    --raw-dir ~/datasets_ep0010 \
    --repo-id local/real_pick_cube_ep0010 \
    --robot-type Unitree_G1_ArmOnly_HeadCam \
    --no-push-to-hub
```

复制时直接改名 `episode_0000`，**就不用跑 `sort_and_rename_folders.py`** ——
那个脚本会**原地重命名**，会动到你的原始采集数据。

| 参数 | 说明 |
|---|---|
| `--raw-dir` | 任务目录的**父目录**（转换器是两层 glob） |
| `--repo-id` | 数据集名，本地用 `local/xxx` 即可 |
| `--robot-type` | 见上表。**是中划线**，README 里的 `--robot_type` 是旧写法 |
| `--no-push-to-hub` | 不上传。README 的 `--push_to_hub false` 会报错 |
| `--mode` | `video`（默认）/ `image` |

输出：`~/.cache/huggingface/lerobot/local/<repo-id>/`

`[事实]` episode_0010 实测：390 帧、5 秒完成、6.6 MB（源 37 MB）、`action` 为 `[14] float32`。

> ⚠️ 必须在 `unitree_lerobot` 环境跑。该环境自带 ffmpeg 7.1.1（含 `libsvtav1`）；
> 系统的 `/usr/bin/ffmpeg` 是 4.4.2 不支持，会报 `Unknown encoder 'libsvtav1'`。

---

## ⑧ 真机运行

### 先导出轻量轨迹包

```bash
conda activate tv
cd ~/xr_teleoperate

python teleop/replay_episode.py \
  --file "teleop/utils/data/pick cube/episode_0010/data.json" \
  --export real0010.npz
```

`[事实]` 丢弃图像/states/sim_state，体积可降到几十 KB。**回放不需要图像。**

### 按顺序来，别跳步 `[未验证]`

```bash
# 步骤 1：网络
ping -c 3 192.168.123.161

# 步骤 2：只读连通检查（零风险，不发任何指令）
python teleop/replay_episode.py --check --net enx00e04c3604a8

# 步骤 3：最小动作（约 11 秒，只挪到第 1 帧再回零）
python teleop/replay_episode.py --file real0010.npz --net enx00e04c3604a8 \
  --end 1 --ramp 8 --speed 0.3 --go-home

# 步骤 4：短段慢放
python teleop/replay_episode.py --file real0010.npz --net enx00e04c3604a8 \
  --end 200 --speed 0.3 --ramp 5 --go-home

# 步骤 5：全程
python teleop/replay_episode.py --file real0010.npz --net enx00e04c3604a8 \
  --speed 0.3 --ramp 5 --go-home
```

脚本会停下来打印确认清单，**输入 `s` 才开始动**。

### 真机参数速查

| 参数 | 建议值 | 说明 |
|---|---|---|
| `--net` | `enx00e04c3604a8` | 网卡名。**不加 `--sim` 就是真机** |
| `--speed` | **0.3** | 倍速，首测务必降速 |
| `--ramp` | **5** | 从当前位姿平滑挪到第一帧的秒数 |
| `--go-home` | 加上 | 结束（含 Ctrl+C）自动回零 |
| `--velocity-limit` | 默认 15 | 真机默认 15 rad/s，**>22 拒绝启动** |
| `--end` / `--start` | 按需 | 截取帧范围 |
| `--check` | — | 只读检查，不发指令 |
| `--dry-run` | — | 只出报告，不建 DDS |

`[事实]` 真机模式会自动调 `Enter_Debug_Mode()`，失败则**拒绝启动**（CHANGE-20260821-001）——
不进 debug 模式的话内置运控会和脚本抢 `rt/lowcmd`，表现为手臂抽搐。

### ⚠️ debug 模式 vs motion 模式（站立真机必读）

`[事实]` `robot_arm.py:104-107`：`motion_mode` 决定发哪个话题。

| | debug 模式（默认） | motion 模式（`--motion`） |
|---|---|---|
| 话题 | `rt/lowcmd` | `rt/arm_sdk` |
| 内置运控 | **被释放** | **继续运行** |
| 腿部 | 锁在当前角度（kp=300），**无平衡反馈** | 运控继续管，**平衡还在** |
| 手臂 | 完全接管 | 通过 `kNotUsedJoint0.q = 1.0` 权重叠加 |
| 需要 `Enter_Debug_Mode()` | 是 | **否**（两者互斥） |

`[事实]` `robot_arm.py:148` 在 motion 模式下跳过锁腿（`id.value <= kRightAnkleRoll`）；`:202` 设 arm_sdk 权重为 1.0。
`[结论]` **机器人站立时建议用 `--motion`** —— debug 模式下腿被冻在固定角度，没有任何平衡响应。
机器人被吊起或坐姿固定时，两种都可以。
`[未验证]` 两种模式均未在真机上实跑对比。

```bash
python teleop/replay_episode.py --file real0010.npz --net enx00e04c3604a8 \
  --motion --end 200 --speed 0.3 --ramp 5 --go-home
```

---

### 备选：官方 `unitree_lerobot/eval_robot/replay_robot.py`

需要先走完 ⑦ 的 LeRobot 转换（它只读数据集，不认 `data.json` / `.npz`）。

```bash
conda activate unitree_lerobot
cd ~/unitree_lerobot

python unitree_lerobot/eval_robot/replay_robot.py \
    --repo_id=local/real_pick_cube_ep0010 \
    --root="" \
    --episodes=0 \
    --frequency=30 \
    --arm="G1_29" \
    --ee="" \
    --motion=true \
    --visualization=false
```

| 参数 | 说明 |
|---|---|
| `--repo_id` | 数据集名 |
| `--root` | 数据集根目录，留空用默认缓存 |
| `--episodes` | 第几条 episode（不是数量） |
| `--frequency` | 回放频率。**必须与数据的 fps 一致**，否则等于变速 |
| `--arm` | `G1_29` / `G1_23` |
| `--ee` | `dex3` / `dex1` / `inspire1` / `brainco`；**数据无手部时留空** |
| `--motion` | 同上表 |
| `--visualization` | true 时需要 PC2 的图像服务在跑；false 则完全不碰相机 |

**注意 `--frequency` 必须匹配数据帧率。** episode_0010 是 30 fps 录的，
填 45 就是 **1.5 倍速** —— 所有关节的指令速度全部 ×1.5。真机首测不要这么做。

`[事实]` 该脚本原本开箱即崩（`AttributeError: image_host`），已由 CHANGE-20260821-004 修复。
`[结论]` 但它仍**没有**：回零、异常兜底、只读连通检查、`--speed` 慢放、逐关节误差输出。
安全性低于 `replay_episode.py`，建议仅在需要与官方流程对齐时使用。

### 现场安全清单

1. **手上握着遥控器**，随时能切阻尼
2. 机器人站稳或吊起，双臂活动范围内无人无物
3. 有第二个人在旁边
4. 首次务必 `--speed 0.3`
5. 轨迹若采自仿真 → 真机上大概率抓空，但手臂会照走完，属正常

---

## 常见错误速查

| 报错 / 现象 | 原因 | 解法 |
|---|---|---|
| `does not contain a valid URDF model` | 不在 `teleop/` 目录 | `cd ~/xr_teleoperate/teleop` |
| `does not match an available interface` | 网卡名错或网卡未起 | `ip -br addr` 查实际网卡 |
| `channel factory init error` | 同上 | 同上 |
| 按 `s` 没反应 | 没加 `--record`，或还没按 `r` | 先 `r` 再 `s` |
| PICO 打不开网页 | IP 填成机器人的了 / 防火墙 | 用**主机** IP；`sudo ufw allow 8012` |
| 录出来没有手部数据 | 漏了 `--ee dex3` | 重录，补不回来 |
| `rt/lowstate` 收不到 | 机器人服务没起 / 未激活 | 查机器人本体与遥控器状态 |
| SSH `HOST IDENTIFICATION HAS CHANGED` | 该 IP 后面的机器换了 | 确认后 `ssh-keygen -R <IP>` |
| `ModuleNotFoundError: tyro` | conda 环境错 | `conda activate unitree_lerobot` |
| 仿真里误差恒定 0.6–1.0 rad | 实时因子 1/42 | 加 `--wait-track`（PROBLEM-011） |
| 仿真里某关节顶住不动 | 撞上场景桌子 | `--clear_obstacles` |

---

## 尚未验证

| 项 | 状态 |
|---|---|
| ⑧ 真机回放全部步骤 | `[未验证]` |
| G1 在 lowcmd 断流后的行为 | `[未验证]`，见 09_REAL_DEPLOY.md 步骤 7 |
| 前馈力矩重力补偿 | 未实施，真机手臂会下垂，末端偏约 6 cm |
| 仿真刚度 400 vs 真机 kp 80 | 未处理，**仿真结论对真机偏乐观** |

---

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-08-22 | 初版。①~⑦ 的命令与参数均已核对源码；⑧ 真机部分未执行。 |
