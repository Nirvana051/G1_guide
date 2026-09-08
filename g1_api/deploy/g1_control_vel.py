#!/usr/bin/env python3
"""G1 速度桥（g1_api 管理版，取代课程 unitree_sdk2_python/example 下的同名脚本）。

- 订阅 /cmd_vel，低通滤波后 LocoClient.Move(vx, vy, wz) 下发
- 与课程版的差异（都可用环境变量覆盖，见下）：
    限幅   vx 0.5→0.8, wz 0.7→0.8（提速；TEB 规划上限已对齐 0.8）
    死区   vx 0.2→0.1, vy 0.2→0.1, wz 0.3→0.15
          课程死区过大：TEB 起步/近障碍的小指令被置零，满 0.1s 进锁定，
          表现为"目标在身后原地转不动"、窄道站桩
- 环境变量：G1_VEL_MAX_VX/VY/WZ, G1_VEL_DEADBAND_VX/VY/WZ, G1_VEL_ALPHA
- 用法（robot_velbridge.sh 已接好）：python3 g1_control_vel.py eth0
"""
import os
import sys

import rospy
from geometry_msgs.msg import Twist

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        rospy.logwarn("bad %s=%r, using %.2f", name, os.environ.get(name), default)
        return default


class VelFilter:
    """简单低通滤波器，避免速度突变"""

    def __init__(self, alpha: float = 0.15):
        self.alpha = alpha
        self.vx_filt = 0.0
        self.vy_filt = 0.0
        self.wz_filt = 0.0

    def update(self, vx: float, vy: float, wz: float):
        self.vx_filt = self.alpha * vx + (1 - self.alpha) * self.vx_filt
        self.vy_filt = self.alpha * vy + (1 - self.alpha) * self.vy_filt
        self.wz_filt = self.alpha * wz + (1 - self.alpha) * self.wz_filt
        return self.vx_filt, self.vy_filt, self.wz_filt

    def reset(self):
        self.vx_filt = 0.0
        self.vy_filt = 0.0
        self.wz_filt = 0.0


class G1VelController:
    def __init__(self, iface: str = "eth0", control_freq: float = 50.0,
                 filter_alpha: float = 0.15):
        rospy.loginfo("========================================")
        rospy.loginfo("Initializing G1 Velocity Controller (g1_api bridge)...")
        rospy.loginfo("========================================")

        ChannelFactoryInitialize(0, iface)

        self.client = LocoClient()
        self.client.SetTimeout(10.0)
        try:
            self.client.Init()
        except Exception as e:  # noqa: BLE001
            rospy.logerr("[ERROR] Robot connection failed: %s", e)
            sys.exit(-1)

        self.dt = 1.0 / control_freq
        self.deadband_vx = _env_f("G1_VEL_DEADBAND_VX", 0.1)
        self.deadband_vy = _env_f("G1_VEL_DEADBAND_VY", 0.1)
        self.deadband_wz = _env_f("G1_VEL_DEADBAND_WZ", 0.15)

        self.max_vx = _env_f("G1_VEL_MAX_VX", 0.8)   # m/s
        self.max_vy = _env_f("G1_VEL_MAX_VY", 0.2)   # m/s
        self.max_wz = _env_f("G1_VEL_MAX_WZ", 0.8)   # rad/s

        # 步态配平（trim）：G1 行走存在固有横向漂移（bag 实测蟹行角约 -6~-8°，
        # 即以 ~0.04 m/s 向右滑，TEB 只能用身体左偏代偿→"歪着走"）。
        # 前进时叠加一个恒定小 vy 抵消漂移；起始试 +0.04，越调越歪就反号。
        self.trim_vy = _env_f("G1_VEL_TRIM_VY", 0.0)
        self.trim_wz = _env_f("G1_VEL_TRIM_WZ", 0.0)

        self.cmd_vx = 0.0
        self.cmd_vy = 0.0
        self.cmd_wz = 0.0

        self.filt = VelFilter(alpha=_env_f("G1_VEL_ALPHA", filter_alpha))

        self.is_stopped = False
        self.stop_counter = 0
        self.stop_counter_max = 5  # 连续 N 帧全零判定停止

        # 调试统计（G1_VEL_DEBUG=1 时逐秒打印；照常驱动机器人）
        self.debug = os.environ.get("G1_VEL_DEBUG", "") in ("1", "true", "yes")
        self.n_msgs = 0
        self.n_vy = 0            # vy≠0 指令数（"歪着走"的指令性来源）
        self.n_wz_deadband = 0   # wz 被死区清零次数（"一节节扭"的量化来源）
        self.last_sent = (0.0, 0.0, 0.0)

        self.vel_sub = rospy.Subscriber("/cmd_vel", Twist, self._vel_cb)
        self.control_timer = rospy.Timer(rospy.Duration(self.dt), self._control_loop)
        if self.debug:
            rospy.Timer(rospy.Duration(1.0), self._debug_report)
            rospy.loginfo("G1_VEL_DEBUG=1：逐秒打印 指令→实发 与统计（正常驱动）")

        rospy.loginfo("G1 Velocity Controller started.")
        rospy.loginfo("  Max vx/vy/wz     : %.2f / %.2f / %.2f", self.max_vx, self.max_vy, self.max_wz)
        rospy.loginfo("  Deadband vx/vy/wz: %.2f / %.2f / %.2f",
                      self.deadband_vx, self.deadband_vy, self.deadband_wz)

    # ------------------------------------------------------------- callbacks --

    def _vel_cb(self, msg: Twist):
        self.cmd_vx = msg.linear.x
        self.cmd_vy = msg.linear.y
        self.cmd_wz = msg.angular.z
        if self.debug:
            self.n_msgs += 1
            if abs(msg.linear.y) > 1e-3:
                self.n_vy += 1
            if 1e-3 < abs(msg.angular.z) < self.deadband_wz:
                self.n_wz_deadband += 1

        if self.is_stopped:
            if (abs(self.cmd_vx) >= self.deadband_vx or
                    abs(self.cmd_vy) >= self.deadband_vy or
                    abs(self.cmd_wz) >= self.deadband_wz):
                self.is_stopped = False
                self.filt.reset()
                rospy.loginfo("[G1] Unlocked: new motion command received")

    # --------------------------------------------------------------- control --

    def _control_loop(self, event):
        # 死区裁剪
        raw_vx = self.cmd_vx if abs(self.cmd_vx) >= self.deadband_vx else 0.0
        raw_vy = self.cmd_vy if abs(self.cmd_vy) >= self.deadband_vy else 0.0
        raw_wz = self.cmd_wz if abs(self.cmd_wz) >= self.deadband_wz else 0.0
        if abs(raw_vx) > 0.1:  # 配平只在行进中叠加（死区之后，不被死区吃掉）
            raw_vy += self.trim_vy
            raw_wz += self.trim_wz

        # 全部静止 -> 进入锁定
        if abs(raw_vx) < 0.001 and abs(raw_vy) < 0.001 and abs(raw_wz) < 0.001:
            self.stop_counter += 1
        else:
            self.stop_counter = 0

        if self.stop_counter >= self.stop_counter_max:
            if not self.is_stopped:
                rospy.loginfo("[G1] Locked: stopping")
            self.is_stopped = True
            self.last_sent = (0.0, 0.0, 0.0)
            self.client.Move(0.0, 0.0, 0.0)
            return

        if self.is_stopped:
            return

        fvx, fvy, fwz = self.filt.update(raw_vx, raw_vy, raw_wz)

        fvx = max(-self.max_vx, min(self.max_vx, fvx))
        fvy = max(-self.max_vy, min(self.max_vy, fvy))
        fwz = max(-self.max_wz, min(self.max_wz, fwz))

        self.last_sent = (fvx, fvy, fwz)
        self.client.Move(fvx, fvy, fwz)

    def _debug_report(self, event):
        flags = []
        if abs(self.cmd_vy) > 1e-3:
            flags.append("VY!=0(横移→歪)")
        if 1e-3 < abs(self.cmd_wz) < self.deadband_wz:
            flags.append("wz入死区(→扭)")
        if self.is_stopped:
            flags.append("LOCKED")
        rospy.loginfo(
            "指令(%.2f,%.2f,%.2f) → 实发(%.2f,%.2f,%.2f) | %dHz | 累计: vy≠0 ×%d, wz入死区 ×%d%s",
            self.cmd_vx, self.cmd_vy, self.cmd_wz,
            self.last_sent[0], self.last_sent[1], self.last_sent[2],
            self.n_msgs, self.n_vy, self.n_wz_deadband,
            (" [" + ", ".join(flags) + "]") if flags else "")
        self.n_msgs = 0


# ----------------------------------------------------------------------- main --

def main():
    rospy.init_node("g1_velocity_controller", anonymous=False)

    if len(sys.argv) > 1:
        iface = sys.argv[1]
    else:
        iface = rospy.get_param("~interface", "eth0")
    freq = rospy.get_param("~control_freq", 50.0)
    alpha = rospy.get_param("~filter_alpha", 0.15)

    G1VelController(iface=iface, control_freq=freq, filter_alpha=alpha)

    try:
        rospy.spin()
    except rospy.ROSInterruptException:
        pass


if __name__ == "__main__":
    main()
