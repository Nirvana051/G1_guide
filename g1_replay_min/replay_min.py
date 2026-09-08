#!/usr/bin/env python3
"""
===============================================================================
replay_min.py —— G1 数据集回放的最小实现（自包含，不依赖 lerobot / torch）
===============================================================================

【它做什么】
    把一条录好的双臂关节角序列，按固定频率逐帧通过 DDS 下发给 G1 真机。
    功能等价于 unitree_lerobot/eval_robot/replay_robot.py 的手臂部分
    （--ee="" 时），但砍掉了所有回放用不到的依赖。

【砍掉了什么，为什么能砍】
    lerobot + torch + torchvision + torchcodec + nvidia/* + triton   约 6.1 GB
        回放只需要数据集里 action 那一列。它就是个 parquet 表，
        用 pyarrow（或预先导出的 .npy）直接读即可，用不着整个 LeRobotDataset。
    casadi + 整个 IK 求解器
        回放的目标关节角来自数据集，不需要解 IK。只用到重力补偿力矩，
        那是 pinocchio 的 rnea 一个函数调用。
    52 MB 的 mesh 文件
        rnea 只用 URDF 里的惯量参数，不加载几何。已验证：
        pin.buildModelFromUrdf(不带mesh) 与 RobotWrapper(带mesh) 的
        rnea 结果逐位一致。

【必需依赖】
    numpy, cyclonedds, unitree_sdk2py
【可选依赖】
    pinocchio  —— 重力补偿。缺失时自动降级为零力矩（手臂会下垂 2~3 度）
    pyarrow    —— 直接读 .parquet。只用 .npy 的话不需要

【用法】
    python replay_min.py --actions data/real_pick_cube_ep0010.npy \
        --frequency 30 --motion

【安全】
    本脚本直接驱动真机关节。运行前确认：机器人周围无人、急停在手边。
"""

import argparse
import os
import sys
import threading
import time
from enum import IntEnum

import numpy as np

# --------------------------------------------------------------------------
# DDS：unitree_sdk2py 是必需的，缺了没法和机器人通信
# --------------------------------------------------------------------------
try:
    from unitree_sdk2py.core.channel import (
        ChannelFactoryInitialize,
        ChannelPublisher,
        ChannelSubscriber,
    )
    from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_ as hg_LowCmd
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_ as hg_LowState
    from unitree_sdk2py.utils.crc import CRC
except ImportError as e:
    sys.exit(f"[FATAL] 缺少 unitree_sdk2py: {e}\n"
             f"        安装: pip install -e <unitree_sdk2_python 源码目录>")

# --------------------------------------------------------------------------
# pinocchio：可选。只用它算重力补偿力矩
# --------------------------------------------------------------------------
try:
    import pinocchio as pin
    HAS_PIN = True
except ImportError:
    HAS_PIN = False


# ============================== 常量 ======================================

# DDS 网卡绑定。本机有多张网卡在 192.168.123.0/24 时，CycloneDDS 的
# autodetermine 会挑错网卡（ping 通但 DDS 不通）。用环境变量指定：
#   export UNITREE_NIC=enp5s0
_NIC = os.environ.get("UNITREE_NIC") or None

kTopicLowCommand_Debug = "rt/lowcmd"    # 调试模式（运控服务停止，须吊装）
kTopicLowCommand_Motion = "rt/arm_sdk"  # 运控模式（运控服务运行中）
kTopicLowState = "rt/lowstate"

G1_29_NUM_MOTORS = 35
ARM_DOF = 14


class JointIndex(IntEnum):
    """G1 29-DoF 全身电机编号（LowCmd/LowState 里的下标）。"""
    kLeftHipPitch = 0
    kLeftHipRoll = 1
    kLeftHipYaw = 2
    kLeftKnee = 3
    kLeftAnklePitch = 4
    kLeftAnkleRoll = 5
    kRightHipPitch = 6
    kRightHipRoll = 7
    kRightHipYaw = 8
    kRightKnee = 9
    kRightAnklePitch = 10
    kRightAnkleRoll = 11
    kWaistYaw = 12
    kWaistRoll = 13
    kWaistPitch = 14
    kLeftShoulderPitch = 15
    kLeftShoulderRoll = 16
    kLeftShoulderYaw = 17
    kLeftElbow = 18
    kLeftWristRoll = 19
    kLeftWristPitch = 20
    kLeftWristYaw = 21
    kRightShoulderPitch = 22
    kRightShoulderRoll = 23
    kRightShoulderYaw = 24
    kRightElbow = 25
    kRightWristRoll = 26
    kRightWristPitch = 27
    kRightWristYaw = 28
    # arm_sdk 模式下 29 号被借用作「权重」：1.0=SDK 接管双臂，0.0=交还运控
    kNotUsedJoint0 = 29


# 双臂 14 个关节，顺序 = 数据集 action 的前 14 维
ARM_JOINTS = [
    JointIndex.kLeftShoulderPitch, JointIndex.kLeftShoulderRoll,
    JointIndex.kLeftShoulderYaw, JointIndex.kLeftElbow,
    JointIndex.kLeftWristRoll, JointIndex.kLeftWristPitch, JointIndex.kLeftWristYaw,
    JointIndex.kRightShoulderPitch, JointIndex.kRightShoulderRoll,
    JointIndex.kRightShoulderYaw, JointIndex.kRightElbow,
    JointIndex.kRightWristRoll, JointIndex.kRightWristPitch, JointIndex.kRightWristYaw,
]

# 肩、肘属于「弱电机」，用低增益；腕部再低一档
WEAK_MOTORS = {
    JointIndex.kLeftAnklePitch, JointIndex.kRightAnklePitch,
    JointIndex.kLeftShoulderPitch, JointIndex.kLeftShoulderRoll,
    JointIndex.kLeftShoulderYaw, JointIndex.kLeftElbow,
    JointIndex.kRightShoulderPitch, JointIndex.kRightShoulderRoll,
    JointIndex.kRightShoulderYaw, JointIndex.kRightElbow,
}
WRIST_MOTORS = {
    JointIndex.kLeftWristRoll, JointIndex.kLeftWristPitch, JointIndex.kLeftWristYaw,
    JointIndex.kRightWristRoll, JointIndex.kRightWristPitch, JointIndex.kRightWristYaw,
}

# URDF 里除双臂外全部锁死（腿/腰/手指），留下 14 个手臂自由度
LOCKED_JOINTS = [
    "left_hip_pitch_joint", "left_hip_roll_joint", "left_hip_yaw_joint",
    "left_knee_joint", "left_ankle_pitch_joint", "left_ankle_roll_joint",
    "right_hip_pitch_joint", "right_hip_roll_joint", "right_hip_yaw_joint",
    "right_knee_joint", "right_ankle_pitch_joint", "right_ankle_roll_joint",
    "waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint",
] + [
    f"{side}_hand_{part}"
    for side in ("left", "right")
    for part in ("thumb_0_joint", "thumb_1_joint", "thumb_2_joint",
                 "index_0_joint", "index_1_joint",
                 "middle_0_joint", "middle_1_joint")
]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================ 重力补偿 =====================================

class GravityComp:
    """用 pinocchio 的 rnea 算「维持当前位姿所需的关节力矩」。

    rnea(model, data, q, dq=0, ddq=0) 即广义重力力矩 G(q)。把它作为前馈力矩
    下发，位置环就只需要负责跟踪误差，手臂不会因自重下垂。

    只建模型不建几何 —— buildModelFromUrdf 不读 mesh，所以 assets 目录里
    只要一个 .urdf 文件（52 KB），不需要 52 MB 的 meshes。
    """

    def __init__(self, urdf_path):
        full = pin.buildModelFromUrdf(urdf_path)
        lock_ids = [full.getJointId(n) for n in LOCKED_JOINTS if full.existJointName(n)]
        self.model = pin.buildReducedModel(full, lock_ids, np.zeros(full.nq))
        self.data = self.model.createData()
        if self.model.nq != ARM_DOF:
            raise RuntimeError(
                f"锁关节后剩 {self.model.nq} 个自由度，期望 {ARM_DOF}。URDF 对不上？")
        log(f"重力补偿已启用 (pinocchio {pin.__version__}, {self.model.nq} DoF)")

    def solve_tau(self, q):
        return pin.rnea(self.model, self.data, q, np.zeros(ARM_DOF), np.zeros(ARM_DOF))


class NoGravityComp:
    """pinocchio 缺失时的降级实现：前馈力矩恒为零。"""

    def solve_tau(self, q):
        return np.zeros(ARM_DOF)


# ============================ 手臂控制器 ===================================

class DataBuffer:
    def __init__(self):
        self._data = None
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            return self._data

    def set(self, d):
        with self._lock:
            self._data = d


class G1ArmController:
    """G1 双臂的 DDS 控制器。

    两个后台线程：
      _subscribe_loop  ~500Hz 读 rt/lowstate，缓存全身电机状态
      _publish_loop    250Hz  把 q_target 限速后填进 LowCmd 发出去

    上层只需调 ctrl_dual_arm(q, tau) 写共享目标，发布线程自己按节拍发。
    """

    def __init__(self, motion_mode=False, velocity_limit=20.0):
        log("初始化 G1ArmController...")
        self.motion_mode = motion_mode
        self.arm_velocity_limit = velocity_limit
        self.control_dt = 1.0 / 250.0

        self.kp_low, self.kd_low = 80.0, 3.0        # 肩/肘
        self.kp_wrist, self.kd_wrist = 40.0, 1.5    # 腕
        self.kp_high, self.kd_high = 300.0, 3.0     # 其余（腰等）

        self.q_target = np.zeros(ARM_DOF)
        self.tauff_target = np.zeros(ARM_DOF)
        self.ctrl_lock = threading.Lock()

        ChannelFactoryInitialize(0, _NIC)
        topic = kTopicLowCommand_Motion if motion_mode else kTopicLowCommand_Debug
        log(f"发布话题: {topic}   网卡: {_NIC or 'autodetermine'}")

        self.lowcmd_publisher = ChannelPublisher(topic, hg_LowCmd)
        self.lowcmd_publisher.Init()
        self.lowstate_subscriber = ChannelSubscriber(kTopicLowState, hg_LowState)
        self.lowstate_subscriber.Init()
        self.lowstate_buffer = DataBuffer()

        threading.Thread(target=self._subscribe_loop, daemon=True).start()

        waited = 0.0
        while self.lowstate_buffer.get() is None:
            time.sleep(0.1)
            waited += 0.1
            if waited > 5.0:
                log("[WARN] 等待 rt/lowstate 超过 5 秒 —— 检查网线/静态IP/UNITREE_NIC")
                waited = 0.0
        log("已订阅 rt/lowstate")

        self.crc = CRC()
        self.msg = unitree_hg_msg_dds__LowCmd_()
        self.msg.mode_pr = 0
        self.msg.mode_machine = self.lowstate_subscriber.Read().mode_machine

        all_q = self.get_all_motor_q()
        log(f"当前双臂关节角:\n  {np.round(self.get_current_dual_arm_q(), 4)}")

        self._setup_gains(all_q)

        # 关键：目标先设成「当前实测角」，而不是零位。
        # 发布线程下一行就启动，若 q_target 还是零位，手臂会在用户确认之前
        # 就自己朝零位跑 —— 而 G1 的零位是「小臂向前平举」，表现为突然抬手。
        self.q_target = self.get_current_dual_arm_q()

        threading.Thread(target=self._publish_loop, daemon=True).start()
        log("G1ArmController 就绪（手臂保持原位，等待指令）")

    def _setup_gains(self, all_q):
        arm_set = {j.value for j in ARM_JOINTS}
        for jid in JointIndex:
            if jid.value >= G1_29_NUM_MOTORS:
                continue
            self.msg.motor_cmd[jid].mode = 1
            if jid.value in arm_set:
                if jid in WRIST_MOTORS:
                    kp, kd = self.kp_wrist, self.kd_wrist
                else:
                    kp, kd = self.kp_low, self.kd_low
            elif self.motion_mode and jid.value <= JointIndex.kRightAnkleRoll.value:
                # arm_sdk 模式下腿部由运控服务实时平衡，我们不发力矩，
                # 否则会和平衡控制器打架
                kp, kd = 0.0, 0.0
            elif jid in WEAK_MOTORS:
                kp, kd = self.kp_low, self.kd_low
            else:
                kp, kd = self.kp_high, self.kd_high
            self.msg.motor_cmd[jid].kp = kp
            self.msg.motor_cmd[jid].kd = kd
            self.msg.motor_cmd[jid].q = all_q[jid.value]

    def _subscribe_loop(self):
        while True:
            msg = self.lowstate_subscriber.Read()
            if msg is not None:
                self.lowstate_buffer.set(
                    np.array([msg.motor_state[i].q for i in range(G1_29_NUM_MOTORS)]))
            time.sleep(0.002)

    def _clip_target(self, target_q):
        """把单周期位移限制在 velocity_limit * control_dt 之内。

        注意这是对「命令」限速，不是对实际手臂限速。默认 20 rad/s 下
        单周期最大 0.08 rad —— 一个 0.87 rad 的跳变约 44 ms 就发完，
        表现为手臂猛甩。要柔和就把 --velocity-limit 调小。
        """
        current = self.get_current_dual_arm_q()
        delta = target_q - current
        scale = np.max(np.abs(delta)) / (self.arm_velocity_limit * self.control_dt)
        return current + delta / max(scale, 1.0)

    def _publish_loop(self):
        if self.motion_mode:
            # arm_sdk 权重拉满：双臂交给本 SDK，其余身体仍归运控服务
            self.msg.motor_cmd[JointIndex.kNotUsedJoint0].q = 1.0
        while True:
            t0 = time.time()
            with self.ctrl_lock:
                q_t, tau_t = self.q_target, self.tauff_target
            clipped = self._clip_target(q_t)
            for i, jid in enumerate(ARM_JOINTS):
                self.msg.motor_cmd[jid].q = clipped[i]
                self.msg.motor_cmd[jid].dq = 0
                self.msg.motor_cmd[jid].tau = tau_t[i]
            self.msg.crc = self.crc.Crc(self.msg)
            self.lowcmd_publisher.Write(self.msg)
            time.sleep(max(0, self.control_dt - (time.time() - t0)))

    def ctrl_dual_arm(self, q_target, tauff_target):
        with self.ctrl_lock:
            self.q_target = np.asarray(q_target, dtype=np.float64)
            self.tauff_target = np.asarray(tauff_target, dtype=np.float64)

    def get_all_motor_q(self):
        return self.lowstate_buffer.get()

    def get_current_dual_arm_q(self):
        all_q = self.lowstate_buffer.get()
        return np.array([all_q[j.value] for j in ARM_JOINTS])

    def release_to_motion_service(self, seconds=2.0):
        """arm_sdk 模式收尾：把权重从 1 缓降到 0，把双臂交还运控服务。"""
        if not self.motion_mode:
            return
        log("把 arm_sdk 权重降到 0，交还运控服务...")
        steps = 100
        for w in np.linspace(1.0, 0.0, steps):
            self.msg.motor_cmd[JointIndex.kNotUsedJoint0].q = float(w)
            time.sleep(seconds / steps)


# ============================== 数据读取 ===================================

def load_actions(path):
    """读取动作序列，返回 (N, >=14) 的 float64 数组。

    支持两种格式：
      .npy      —— 只需要 numpy
      .parquet  —— 需要 pyarrow；自动取 action 列
    """
    if path.endswith(".npy"):
        arr = np.load(path)
    elif path.endswith(".parquet"):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            sys.exit("[FATAL] 读 .parquet 需要 pyarrow：pip install pyarrow\n"
                     "        或改用预先导出的 .npy")
        table = pq.read_table(path, columns=["action"])
        arr = np.stack(table.column("action").to_numpy(zero_copy_only=False))
    else:
        sys.exit(f"[FATAL] 不认识的格式: {path}（支持 .npy / .parquet）")

    arr = np.asarray(arr, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] < ARM_DOF:
        sys.exit(f"[FATAL] action 形状 {arr.shape} 不对，需要 (N, >={ARM_DOF})")
    return arr


# ================================ 连播模式 =================================

def run_sequence(args):
    """顺序播放多条轨迹：一次 DDS 会话、arm_sdk 权重全程保持，仅最后交还运控。

    与逐条起子进程的区别：中间不会出现「权重 1→0→1」的交还-回中-再接管，
    多个动作衔接处只是按 velocity_limit 平滑滑到下一条的起始位姿。
    """
    import json as _json

    here = os.path.dirname(os.path.abspath(__file__))
    items = _json.loads(args.sequence_json)
    if not items:
        sys.exit("[FATAL] --sequence-json 为空")

    plans = []
    for it in items:
        path = it["file"]
        if not os.path.isabs(path):
            path = os.path.join(here, path)
        actions = load_actions(path)
        plans.append({
            "actions": actions,
            "freq": float(it.get("frequency", 30.0)),
            "vlimit": float(it.get("velocity_limit", 20.0)),
            "name": os.path.basename(path),
        })
    total_s = sum(len(p["actions"]) / p["freq"] for p in plans)
    log("连播 %d 条轨迹，总时长约 %.1f 秒" % (len(plans), total_s))

    urdf_path = args.urdf if os.path.isabs(args.urdf) else os.path.join(here, args.urdf)
    if args.no_gravity_comp or not HAS_PIN:
        gc = NoGravityComp()
    else:
        gc = GravityComp(urdf_path)

    if args.dry_run:
        log("--dry-run：%d 条轨迹数据检查通过" % len(plans))
        return

    arm = G1ArmController(motion_mode=args.motion,
                          velocity_limit=max(p["vlimit"] for p in plans))
    if not args.yes:
        if input("确认机器人周围无人、急停在手边，输入 's' 开始连播: ").strip().lower() != "s":
            log("已取消")
            return

    try:
        for k, p in enumerate(plans):
            arm.arm_velocity_limit = p["vlimit"]
            init_pose = p["actions"][0][:ARM_DOF]
            log("[%d/%d] %s：移动到起始位姿..." % (k + 1, len(plans), p["name"]))
            arm.ctrl_dual_arm(init_pose, gc.solve_tau(init_pose))
            # 第一条给足 init_seconds；后续衔接只需短暂滑移（限速器已保证平滑）
            time.sleep(args.init_seconds if k == 0 else max(0.8, args.init_seconds / 2.0))
            period = 1.0 / p["freq"]
            n = len(p["actions"])
            log("[%d/%d] %s：回放 %d 帧 @ %g Hz" % (k + 1, len(plans), p["name"], n, p["freq"]))
            for idx in range(n):
                loop_t0 = time.perf_counter()
                q = p["actions"][idx][:ARM_DOF]
                arm.ctrl_dual_arm(q, gc.solve_tau(q))
                time.sleep(max(0, period - (time.perf_counter() - loop_t0)))
    except KeyboardInterrupt:
        log("[中断] 收到 Ctrl-C，停在当前位姿")
    arm.release_to_motion_service()
    log("连播结束")


# ================================ 主流程 ===================================

def main():
    ap = argparse.ArgumentParser(
        description="G1 双臂数据集回放（最小实现）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--actions", default="data/real_pick_cube_ep0010.npy",
                    help="动作序列文件（.npy 或 .parquet）")
    ap.add_argument("--urdf", default="assets/g1_body29_hand14.urdf",
                    help="URDF 路径，只用于重力补偿；--no-gravity-comp 时忽略")
    ap.add_argument("--frequency", type=float, default=30.0,
                    help="回放频率 Hz，应与录制频率一致")
    ap.add_argument("--velocity-limit", type=float, default=20.0,
                    help="关节速度上限 rad/s，越小越柔和")
    ap.add_argument("--init-seconds", type=float, default=2.0,
                    help="移动到起始位姿后的等待时间")
    ap.add_argument("--motion", action="store_true",
                    help="运控模式，发 rt/arm_sdk（机器人站立）。不加则为调试模式 rt/lowcmd（须吊装）")
    ap.add_argument("--no-gravity-comp", action="store_true",
                    help="关闭重力补偿，前馈力矩恒为零")
    ap.add_argument("--yes", action="store_true", help="跳过人工确认（不推荐）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不连 DDS、不动机器人，只检查数据和重力补偿")
    ap.add_argument("--sequence-json", default="",
                    help='连播模式：JSON 数组 [{"file","frequency","velocity_limit"}...]。'
                         "顺序播放多条轨迹，中间不交还运控（arm_sdk 权重保持 1），"
                         "仅最后 release 一次——串多个动作不再逐条回中")
    args = ap.parse_args()

    if args.sequence_json:
        run_sequence(args)
        return

    here = os.path.dirname(os.path.abspath(__file__))
    actions_path = args.actions if os.path.isabs(args.actions) else os.path.join(here, args.actions)
    urdf_path = args.urdf if os.path.isabs(args.urdf) else os.path.join(here, args.urdf)

    # ---------------- 1. 数据 ----------------
    actions = load_actions(actions_path)
    n = len(actions)
    log(f"动作序列: {os.path.basename(actions_path)}  {n} 帧 x {actions.shape[1]} 维")
    log(f"回放 {args.frequency} Hz -> 时长约 {n / args.frequency:.1f} 秒")
    peak = np.abs(np.diff(actions[:, :ARM_DOF], axis=0)).max() * args.frequency
    log(f"该频率下峰值关节速度 {peak:.2f} rad/s（--velocity-limit 建议 ≥ {peak * 2:.1f}）")
    if peak * 2 > args.velocity_limit:
        log(f"[WARN] velocity-limit={args.velocity_limit} 偏低，手臂会跟不上、轨迹被抹圆")

    # ---------------- 2. 重力补偿 ----------------
    if args.no_gravity_comp:
        gc = NoGravityComp()
        log("重力补偿：已按 --no-gravity-comp 关闭（手臂会下垂 2~3 度）")
    elif not HAS_PIN:
        gc = NoGravityComp()
        log("[WARN] 未安装 pinocchio，重力补偿降级为零力矩（手臂会下垂 2~3 度）")
    else:
        gc = GravityComp(urdf_path)

    init_pose = actions[0][:ARM_DOF]
    log(f"数据集起始位姿:\n  {np.round(init_pose, 4)}")

    if args.dry_run:
        log("--dry-run：跳过 DDS 与真机动作")
        tau = gc.solve_tau(init_pose)
        log(f"起始位姿的重力补偿力矩:\n  {np.round(tau, 4)}")
        log(f"力矩范围 [{tau.min():.3f}, {tau.max():.3f}] N·m")
        log("✅ dry-run 通过：数据、URDF、重力补偿都正常")
        return

    # ---------------- 3. 连接机器人 ----------------
    arm = G1ArmController(motion_mode=args.motion, velocity_limit=args.velocity_limit)

    current = arm.get_current_dual_arm_q()
    delta_deg = np.degrees(init_pose - current)
    log(f"当前 -> 起始 的位移(度):\n  {np.round(delta_deg, 1)}")
    log(f"最大 {np.abs(delta_deg).max():.1f} 度，"
        f"按 {args.velocity_limit} rad/s 约 "
        f"{np.abs(init_pose - current).max() / args.velocity_limit * 1000:.0f} ms 发完")

    # ---------------- 4. 人工确认 ----------------
    if not args.yes:
        if input("确认机器人周围无人、急停在手边，输入 's' 开始: ").strip().lower() != "s":
            log("已取消")
            return

    # ---------------- 5. 摆到起始位姿 ----------------
    log("移动到起始位姿...")
    arm.ctrl_dual_arm(init_pose, gc.solve_tau(init_pose))
    time.sleep(args.init_seconds)

    # ---------------- 6. 逐帧回放 ----------------
    log(f"开始回放 {n} 帧 @ {args.frequency} Hz")
    period = 1.0 / args.frequency
    t_start = time.perf_counter()
    try:
        for idx in range(n):
            loop_t0 = time.perf_counter()
            q = actions[idx][:ARM_DOF]
            arm.ctrl_dual_arm(q, gc.solve_tau(q))
            if idx % int(max(1, args.frequency)) == 0:
                log(f"  帧 {idx}/{n}  ({idx / n * 100:.0f}%)")
            time.sleep(max(0, period - (time.perf_counter() - loop_t0)))
    except KeyboardInterrupt:
        log("[中断] 收到 Ctrl-C，停在当前位姿")
    else:
        log(f"回放完成，实际耗时 {time.perf_counter() - t_start:.1f} 秒")

    # ---------------- 7. 收尾 ----------------
    # 手臂停在最后一帧位姿。motion 模式下把 arm_sdk 权重降回 0，
    # 让运控服务重新接管双臂；否则进程退出后手臂会失去位置环支撑。
    arm.release_to_motion_service()
    log("结束")


if __name__ == "__main__":
    main()
