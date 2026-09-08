"""ROS topic/service names and defaults for the G1 navigation stack (appendix A.5).

These are configurable in principle; the defaults are the values the course
``navigation.launch`` and ``navigation_sdk.py`` use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

__all__ = ["RosParams", "DEFAULT_ROS_PARAMS"]

#: Topic/service names used by ``Nav2Anywhere`` and the health checks.
DEFAULT_TOPICS = {
    "goal": "/move_base_simple/goal",
    "cancel": "/move_base/cancel",
    "feedback": "/move_base/feedback",
    "global_plan": "/move_base/GlobalPlanner/plan",
    "clear_costmaps": "/move_base/clear_costmaps",
    "cmd_vel": "/cmd_vel",
    "odom": "slam_odom",
    "scan": "/scan",
    "lidar": "/livox/lidar",
    "imu": "/livox/imu",
    "map_2d": "/map_2d",
    "reloc": "/slam_reloc",
    "reloc_check": "/slam_reloc_check",
}

DEFAULT_FRAMES = {
    "parent": "map",
    "child": "body",
}

DEFAULT_NAV = {
    "reach_threshold": 0.2,
    "yaw_threshold": 0.2,
    "fine_yaw_threshold": 0.15,
    "rotate_speed": 0.4133,
    "xy_fine_speed": 0.21,
    "xy_fine_threshold": 0.1,
}


@dataclass(frozen=True)
class RosParams:
    """Resolved ROS names for the real adapter."""

    move_base_ns: str = "/move_base"
    parent_frame: str = "map"
    child_frame: str = "body"
    topics: Tuple[Tuple[str, str], ...] = (
        ("goal", "/move_base_simple/goal"),
        ("cancel", "/move_base/cancel"),
        ("feedback", "/move_base/feedback"),
        ("global_plan", "/move_base/GlobalPlanner/plan"),
        ("clear_costmaps", "/move_base/clear_costmaps"),
        ("cmd_vel", "/cmd_vel"),
        ("odom", "slam_odom"),
        ("scan", "/scan"),
        ("reloc", "/slam_reloc"),
    )

    def topic(self, name: str, default: str = "") -> str:
        for key, value in self.topics:
            if key == name:
                return value
        return default


DEFAULT_ROS_PARAMS = RosParams()
