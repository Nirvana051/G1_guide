# G1 双臂回放 · 最小版

把一条录好的双臂关节角序列，通过 DDS 逐帧下发给 Unitree G1 真机。

功能等价于 `unitree_lerobot/eval_robot/replay_robot.py` 在 `--ee=""`（只跑手臂）时的行为，
但砍掉了所有回放用不到的依赖。

| | 完整版 | 本最小版 |
| --- | --- | --- |
| 运行环境 | conda **9.4 GB** | venv **501 MB**（不要重力补偿则 **118 MB**）|
| 代码 + 资产 + 数据 + SDK | 约 450 MB | **2.0M** |
| 依赖 | lerobot / torch / torchvision / torchcodec / nvidia×14 / triton / casadi / opencv / rerun / draccus … | numpy / cyclonedds / unitree_sdk2py（+ 可选 pinocchio） |
| 需要 conda | 是 | **否**，venv + pip 即可 |
| 需要联网 | 是 | 只在建环境时（约 200 MB），SDK 已自带 |

---

## 1. 为什么能砍掉这么多

**① 甩掉 lerobot + torch 全家（约 6.1 GB）**

回放只需要数据集里 `action` 那一列。它就是个 parquet 表，用 `pyarrow` 直接读、
或预先导出成 `.npy`，用不着整个 `LeRobotDataset`（那会拖进 torch、torchcodec、
datasets、视频解码器……）。完整版里 `nvidia/*` 3.6 GB + `torch` 1.9 GB + `triton` 539 MB
对回放**一次都没被调用**——`replay_robot.py` 里零处 GPU 操作。

**② 甩掉 casadi 和整个 IK 求解器**

回放的目标关节角来自数据集，不需要解 IK。原来的 `G1_29_ArmIK` 类在 `__init__` 里
就构建了一整套 casadi 符号优化问题，而回放只用到它的 `solve_tau()` ——
那不过是 pinocchio 的 `rnea` 一个函数调用。

**③ 甩掉 52 MB 的 mesh**

`rnea` 只用 URDF 里的惯量参数，不加载几何。已实测验证：

```
pin.buildModelFromUrdf(不带mesh)  vs  RobotWrapper.BuildFromURDF(带mesh)
rnea 结果最大差异 = 0.0        逐位一致
```

所以 `assets/` 里只有一个 52 KB 的 `.urdf`。

---

## 2. 目录结构

```
g1_replay_min/                              2.0 MB
├── replay_min.py                            24 KB   自包含：DDS 控制器 + 数据读取 + 重力补偿
├── setup_env.sh                            4.0 KB   一键建环境
├── assets/
│   └── g1_body29_hand14.urdf                52 KB   只用于算重力补偿，不含 mesh
├── data/
│   └── real_pick_cube_ep0010.npy            24 KB   390 帧 × 14 维 float32
├── vendor/
│   └── unitree_sdk2_python/                1.8 MB   SDK 源码 v1.0.1 (commit 65691c8)
├── requirements.txt
└── README.md
```

**整个文件夹拷到 U 盘带走就行** —— SDK 源码已经在 `vendor/` 里，新电脑不需要
再去 GitHub 克隆。

---

## 3. 环境

### 硬性要求：Python 3.10

不是建议，是**必须**。原因是两个关键包的 wheel 覆盖范围（linux x86_64）：

```
cyclonedds 0.10.2   cp37  cp38  cp39  cp310
pin        4.1.0                     cp310  cp311  cp312  cp313  cp314
                                     ─────
                                     交集只有 cp310
```

其它 Python 版本会退化成源码编译并失败，典型报错：

```
Could not locate cyclonedds. Try to set CYCLONEDDS_HOME or CMAKE_PREFIX_PATH
```

（这台机器的系统 python 是 3.14，直接建 venv 就是这么失败的。）

### 方式 A：一条命令（推荐，501 MB，不需要 conda）

```bash
cd g1_replay_min
bash setup_env.sh
```

脚本会：找 Python 3.10 → 建 `.venv` → pip 装 numpy/cyclonedds/pin →
装 `vendor/` 里自带的 SDK → 跑一次 dry-run 自检。

SDK 源码（`unitree_sdk2py` 1.0.1，commit `65691c8`）已经放在 `vendor/` 里，
**不需要联网克隆**。但 numpy/cyclonedds/pin 要从 PyPI 下（约 200 MB）。

装完这样用：

```bash
source .venv/bin/activate
python replay_min.py --dry-run
```

**三种规模，实测值：**

| 方式 | 大小 | 重力补偿 |
| --- | --- | --- |
| `bash setup_env.sh` | **501 MB** | ✅ |
| `NO_PIN=1 bash setup_env.sh` | **118 MB** | ❌ 下垂约 1.8° |
| conda 版（方式 C） | 1.0 GB | ✅ |

### 方式 B：手动敲命令（等价于方式 A）

```bash
# 1) 建 venv，必须 3.10
python3.10 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel

# 2) 依赖。cyclonedds 版本必须锁 0.10.2，unitree_sdk2py 只兼容这个版本
pip install numpy "cyclonedds==0.10.2" "pin==4.1.0"

# 3) SDK。--no-deps 是为了避开它 setup.py 里声明的 opencv-python（回放用不到）
pip install -e vendor/unitree_sdk2_python --no-deps --no-build-isolation

# 4) 验证
python replay_min.py --dry-run
```

没有 `python3.10` 的话：

```bash
sudo apt install python3.10 python3.10-venv
```

### 方式 C：用 conda（1.0 GB，比 venv 大一倍）

只在没法装 Python 3.10、但有 conda 的情况下用：

```bash
conda create -n g1_replay_min -c conda-forge python=3.10 numpy pinocchio -y
conda activate g1_replay_min
pip install "cyclonedds==0.10.2"
pip install -e vendor/unitree_sdk2_python --no-deps --no-build-isolation
python replay_min.py --dry-run
```

conda 版更大，因为 conda-forge 的 pinocchio 会带一整套 C++ 依赖
（libpinocchio 83 MB + libcoal/libboost 49 MB + conda 基础库），
而 PyPI 的 `pin` wheel 是打包好的 10 MB。

### 方式 D：机器上已经有完整的 `unitree_lerobot` 环境

什么都不用装：

```bash
conda activate unitree_lerobot
cd g1_replay_min && python replay_min.py --dry-run
```

---

## 4. 运行

### 第 1 步：dry-run（不连机器人，必做）

```bash
cd g1_replay_min
python replay_min.py --dry-run
```

期望输出：

```
动作序列: real_pick_cube_ep0010.npy  390 帧 x 14 维
回放 30.0 Hz -> 时长约 13.0 秒
该频率下峰值关节速度 1.96 rad/s（--velocity-limit 建议 ≥ 3.9）
重力补偿已启用 (pinocchio 4.1.0, 14 DoF)
起始位姿的重力补偿力矩:
  [-1.8331  2.5227  0.2454 -1.0608 ...]
✅ dry-run 通过：数据、URDF、重力补偿都正常
```

这一步验证了数据、URDF、重力补偿三样，**完全不碰机器人**。

### 第 2 步：配网络

```bash
ip -o -4 addr                                        # 先看网卡叫什么
sudo ip addr add 192.168.123.222/24 dev <网卡名>      # 配到机器人网段
sudo ip link set <网卡名> up
ping -c2 192.168.123.161                             # G1 主控
```

如果**有多张网卡在 192.168.123 网段**，CycloneDDS 的自动选网卡会挑错
（现象：ping 通但 DDS 收不到）。用环境变量指定：

```bash
export UNITREE_NIC=<网卡名>
```

### 第 3 步：机器人准备

| 模式 | 参数 | 话题 | 前提 |
| --- | --- | --- | --- |
| **调试模式** | 不加 `--motion` | `rt/lowcmd` | 运控服务**停止**，机器人**必须吊装**（腿部无力矩，不吊会瘫倒） |
| **运控模式** | `--motion` | `rt/arm_sdk` | 运控服务运行中，机器人站立自平衡 |

确认周围无人、**急停开关拿在手里**。

### 第 4 步：真机回放

```bash
# 运控模式（机器人站着），先用低速
python replay_min.py --motion --frequency 30 --velocity-limit 4.0
```

脚本会先打印「当前 → 起始」的每关节位移，然后停下来等你输入 `s` 确认。
看一眼位移合不合理再敲。

---

## 5. 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--actions` | `data/real_pick_cube_ep0010.npy` | 动作序列，支持 `.npy` 和 `.parquet` |
| `--urdf` | `assets/g1_body29_hand14.urdf` | 只用于重力补偿 |
| `--frequency` | `30.0` | 回放频率 Hz，**应与录制频率一致**，否则动作被整体加减速 |
| `--velocity-limit` | `20.0` | 关节速度上限 rad/s，越小越柔和 |
| `--init-seconds` | `2.0` | 摆到起始位姿后的等待时间 |
| `--motion` | 关 | 运控模式（`rt/arm_sdk`）；不加为调试模式（`rt/lowcmd`） |
| `--no-gravity-comp` | 关 | 强制关闭重力补偿 |
| `--yes` | 关 | 跳过人工确认（**不推荐**，那个确认门的意义是「有人站在急停旁边」） |
| `--dry-run` | 关 | 不连 DDS、不动机器人 |

### `--velocity-limit` 怎么选

脚本启动时会算出该频率下数据需要的峰值速度，并给出建议值（约为峰值的 2 倍）。
本数据集 30 Hz 下峰值 1.96 rad/s，建议 ≥ 3.9。

- **设太低**：手臂跟不上，轨迹被"抹圆"
- **设太高**：所有跳变照单全收，起始位姿那一下会猛甩

> 只降 `--frequency` 不降 `--velocity-limit` 是错的：手臂会「瞬间冲到位 → 干等 →
> 再冲」，变成一顿一顿的抽搐，比原速更危险。

---

## 6. 换自己的数据集

### 用 LeRobot 数据集的 parquet（需要 pyarrow）

```bash
python replay_min.py --actions ~/.cache/huggingface/lerobot/local/<你的数据集>/data/chunk-000/file-000.parquet
```

### 或先导出成 .npy（之后就不需要 pyarrow）

```python
import pyarrow.parquet as pq, numpy as np, glob
src = '~/.cache/huggingface/lerobot/local/<你的数据集>'
f = glob.glob(f'{src}/data/**/*.parquet', recursive=True)[0]
a = np.stack(pq.read_table(f, columns=['action']).column('action').to_numpy(zero_copy_only=False))
np.save('data/my_episode.npy', a.astype(np.float32))
```

**要求**：数组形状 `(N, ≥14)`，前 14 维按这个顺序排列 —

```
左: ShoulderPitch, ShoulderRoll, ShoulderYaw, Elbow, WristRoll, WristPitch, WristYaw
右: ShoulderPitch, ShoulderRoll, ShoulderYaw, Elbow, WristRoll, WristPitch, WristYaw
```

超过 14 维的部分（灵巧手动作）会被忽略——本脚本**不控制末端执行器**。

---

## 7. 故障排查

| 现象 | 原因 / 处理 |
| --- | --- |
| `等待 rt/lowstate 超过 5 秒` | 没配 192.168.123.x 静态 IP，或网线没接，或网卡挑错 → `export UNITREE_NIC=<网卡名>` |
| `缺少 unitree_sdk2py` | `pip install -e <源码目录> --no-deps` |
| `[WARN] 未安装 pinocchio` | 可以忽略（降级为零力矩），或 `conda install -c conda-forge pinocchio` |
| 读 `.parquet` 报缺 pyarrow | `pip install pyarrow`，或改用 `.npy` |
| 手臂突然抬起来 | **G1 的零位是「小臂向前平举」，不是自然下垂。** 本脚本已在启动发布线程前把 `q_target` 设成实测关节角，不会出现这个问题。若你改了这段代码要注意 |
| 起始位姿那一下猛甩 | 调小 `--velocity-limit` |

---

## 8. 已验证 / 未验证

**已验证**（在本机跑过）：

- `--dry-run` 在完整环境和 1.0 GB 最小环境里都通过
- 重力补偿力矩与完整版 `robot_arm_ik.solve_tau()` 的输出**逐位一致**
- 去掉 mesh 后 `rnea` 结果与带 mesh **完全相同**（最大差异 0.0）
- DDS 路径能正常初始化 `ChannelFactory`、建 publisher、订阅 `rt/lowstate`
- 无 pinocchio 时的降级路径正常（力矩全零，环境降到 118 MB）
- **完整的新机器流程**：把文件夹拷到一个全新目录 → `bash setup_env.sh` →
  自动建出 501 MB 的 venv 并通过 dry-run 自检。全程只用 `vendor/` 里自带的 SDK，不联网克隆
- **完整的新机器流程**：把文件夹拷到一个全新目录 → `bash setup_env.sh` →
  自动建出 501 MB 的 venv 并通过自检。全程只用 `vendor/` 里自带的 SDK

**未验证**：

> **没有在真机上跑过。** 本机当前没有接到 G1 的 192.168.123.x 网段，
> 所以「实际驱动电机」这一段只验证到「正确地等待 lowstate」为止。
> 第一次上真机请：**吊装 + 急停在手 + `--velocity-limit 2.0` 起步**。

---

## 9. 这个最小版**不**做什么

- 不控制灵巧手 / 夹爪（完整版的 `--ee=dex3` 等）
- 不做策略推理（那需要 torch，回到完整版）
- 不做 rerun 可视化、不连相机
- 不支持 G1_23 / H1 / H1_2（只有 G1 29-DoF 的关节表）

需要以上任何一项，用完整版 `unitree_lerobot/eval_robot/replay_robot.py`。
