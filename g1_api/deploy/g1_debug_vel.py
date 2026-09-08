#!/usr/bin/env python3
"""调试速度桥：订阅 /cmd_vel、跑与真桥完全相同的处理管线（死区→EMA→限幅），
但【不驱动机器人】——把本来要发给 LocoClient.Move 的指令打印出来并做统计。

用途：机器人站着不动，安全地观察导航栈到底在发什么——
  · vy≠0 的横移指令（"歪着走"的指令性来源）
  · 被 wz 死区清零的小航向修正（"一节一节扭"的量化来源）
  · 锁定/解锁事件、指令频率

用法（终端② 用 robot_debugbridge.sh 代替 robot_velbridge.sh）。
注意：它订阅了 /cmd_vel，所以 velocity_bridge_down 互锁会消失、导航动作能启动，
但机器人不会动——MoveTo 会一直走不到（这正是调试模式的含义）。
"""
import os
import time

import rospy
from geometry_msgs.msg import Twist


def _env_f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class DebugBridge(object):
    def __init__(self):
        # 与 g1_control_vel.py 完全一致的参数（含环境变量覆盖）
        self.dbx = _env_f("G1_VEL_DEADBAND_VX", 0.1)
        self.dby = _env_f("G1_VEL_DEADBAND_VY", 0.1)
        self.dbz = _env_f("G1_VEL_DEADBAND_WZ", 0.08)
        self.mx = _env_f("G1_VEL_MAX_VX", 0.8)
        self.my = _env_f("G1_VEL_MAX_VY", 0.2)
        self.mz = _env_f("G1_VEL_MAX_WZ", 0.8)
        self.alpha = _env_f("G1_VEL_ALPHA", 0.15)
        self.fx = self.fy = self.fz = 0.0
        self.cmd = (0.0, 0.0, 0.0)
        self.n_msgs = 0
        self.n_vy = 0            # vy 非零的指令数（歪着走嫌疑）
        self.n_wz_deadband = 0   # wz 被死区清零的次数（蛇形量化嫌疑）
        self.locked = False
        self.zero_streak = 0
        rospy.Subscriber("/cmd_vel", Twist, self._cb, queue_size=10)
        rospy.Timer(rospy.Duration(0.02), self._tick)   # 50 Hz，与真桥同拍
        rospy.Timer(rospy.Duration(1.0), self._report)
        rospy.loginfo("[debug-bridge] 死区 %.2f/%.2f/%.2f 限幅 %.2f/%.2f/%.2f alpha %.2f"
                      % (self.dbx, self.dby, self.dbz, self.mx, self.my, self.mz, self.alpha))
        rospy.loginfo("[debug-bridge] 只观察不驱动——机器人不会动")

    def _cb(self, msg):
        self.cmd = (msg.linear.x, msg.linear.y, msg.angular.z)
        self.n_msgs += 1
        if abs(msg.linear.y) > 1e-3:
            self.n_vy += 1
        if 1e-3 < abs(msg.angular.z) < self.dbz:
            self.n_wz_deadband += 1

    def _tick(self, _):
        vx, vy, wz = self.cmd
        rvx = vx if abs(vx) >= self.dbx else 0.0
        rvy = vy if abs(vy) >= self.dby else 0.0
        rwz = wz if abs(wz) >= self.dbz else 0.0
        if abs(rvx) < 1e-3 and abs(rvy) < 1e-3 and abs(rwz) < 1e-3:
            self.zero_streak += 1
        else:
            self.zero_streak = 0
            if self.locked:
                self.locked = False
                rospy.loginfo("[debug-bridge] 解锁（收到有效指令）")
        if self.zero_streak >= 5 and not self.locked:
            self.locked = True
            rospy.loginfo("[debug-bridge] 锁定（连续5帧零速）→ 真桥此刻会发 Move(0,0,0)")
        a = self.alpha
        self.fx = a * rvx + (1 - a) * self.fx
        self.fy = a * rvy + (1 - a) * self.fy
        self.fz = a * rwz + (1 - a) * self.fz

    def _report(self, _):
        vx, vy, wz = self.cmd
        out = (max(-self.mx, min(self.mx, self.fx)),
               max(-self.my, min(self.my, self.fy)),
               max(-self.mz, min(self.mz, self.fz)))
        flags = []
        if abs(vy) > 1e-3:
            flags.append("VY!=0(横移→歪)")
        if 1e-3 < abs(wz) < self.dbz:
            flags.append("wz被死区吃掉(→扭)")
        if self.locked:
            flags.append("LOCKED")
        rospy.loginfo(
            "raw(%.2f,%.2f,%.2f) → 真桥会发(%.2f,%.2f,%.2f) | %dHz | 累计: vy≠0 ×%d, wz入死区 ×%d %s"
            % (vx, vy, wz, out[0], out[1], out[2], self.n_msgs, self.n_vy,
               self.n_wz_deadband, (" [" + ", ".join(flags) + "]") if flags else ""))
        self.n_msgs = 0


def main():
    rospy.init_node("g1_debug_velocity_bridge", anonymous=False)
    DebugBridge()
    rospy.spin()


if __name__ == "__main__":
    main()
