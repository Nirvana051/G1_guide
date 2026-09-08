"""Navigation SDK for G1, copied into this project from the course
``agent_server/robot_sdk/navigation_sdk.py`` and made Python-3.8-compatible
(typing annotations instead of ``X | None`` / builtin generics).

ROS1 Noetic implementation, three-stage arrival (coarse reach -> rotate align ->
XY fine adjust), arrival judged by TF distance, not ``/move_base/result``.
All ROS imports are guarded so this module imports cleanly without ROS.
"""

from __future__ import annotations

import math
import time
from typing import Any, Optional, Tuple

try:  # pragma: no cover - exercised only on a machine with ROS
    import rospy
    import tf
    import tf.transformations as tft
    from geometry_msgs.msg import PoseStamped, Twist
    from move_base_msgs.msg import MoveBaseActionFeedback
    from actionlib_msgs.msg import GoalID
    from nav_msgs.msg import Odometry

    ROS_AVAILABLE = True
except Exception:  # pragma: no cover - the dev machine has no ROS
    rospy = None  # type: ignore
    tf = None  # type: ignore
    tft = None  # type: ignore
    PoseStamped = None  # type: ignore
    Twist = None  # type: ignore
    MoveBaseActionFeedback = None  # type: ignore
    GoalID = None  # type: ignore
    Odometry = None  # type: ignore
    ROS_AVAILABLE = False

__all__ = [
    "REACH_THRESHOLD",
    "YAW_THRESHOLD_DEFAULT",
    "FINE_YAW_THRESHOLD",
    "ROTATE_SPEED_DEFAULT",
    "XY_FINE_SPEED",
    "XY_FINE_THRESHOLD",
    "Nav2Anywhere",
    "ROS_AVAILABLE",
]

REACH_THRESHOLD: float = 0.2
YAW_THRESHOLD_DEFAULT: float = 0.2
FINE_YAW_THRESHOLD: float = 0.15
ROTATE_SPEED_DEFAULT: float = 0.4133
XY_FINE_SPEED: float = 0.21
XY_FINE_THRESHOLD: float = 0.1


def _yaw_from_quaternion(ori: Any) -> float:
    x, y, z, w = float(ori.x), float(ori.y), float(ori.z), float(ori.w)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _quaternion_from_yaw(yaw: float) -> Tuple[float, float, float, float]:
    return tft.quaternion_from_euler(0.0, 0.0, yaw)  # type: ignore[misc]


def yaw_from_quaternion_xyzw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class Nav2Anywhere:
    """Navigate via ``/move_base_simple/goal``, judge arrival by TF distance."""

    REACH_THRESHOLD = REACH_THRESHOLD
    YAW_THRESHOLD_DEFAULT = YAW_THRESHOLD_DEFAULT
    FINE_YAW_THRESHOLD = FINE_YAW_THRESHOLD
    ROTATE_SPEED_DEFAULT = ROTATE_SPEED_DEFAULT
    XY_FINE_SPEED = XY_FINE_SPEED
    XY_FINE_THRESHOLD = XY_FINE_THRESHOLD

    _ros_inited = False

    def __init__(
        self,
        parent_frame: str = "map",
        child_frame: str = "body",
        move_base_ns: str = "/move_base",
        reach_threshold: float = REACH_THRESHOLD,
        yaw_threshold_default: float = YAW_THRESHOLD_DEFAULT,
    ) -> None:
        if not ROS_AVAILABLE:
            raise ImportError("rospy/tf are not available; the G1 navigation SDK needs ROS1 Noetic")
        if not Nav2Anywhere._ros_inited:
            try:
                rospy.get_rostime()  # type: ignore[union-attr]
            except Exception:
                rospy.init_node("nav2anywhere", anonymous=True)  # type: ignore[union-attr]
            Nav2Anywhere._ros_inited = True

        self._parent_frame = parent_frame
        self._child_frame = child_frame
        self._reach_threshold = reach_threshold
        self._goal_pose: Optional[Any] = None
        self._tf_listener = tf.TransformListener()  # type: ignore[union-attr]
        self._odom_sub = rospy.Subscriber("slam_odom", Odometry, self._odom_cb, queue_size=10)  # type: ignore[union-attr]
        self._simple_pub = rospy.Publisher("/move_base_simple/goal", PoseStamped, queue_size=1)  # type: ignore[union-attr]
        self._feedback_sub = rospy.Subscriber("%s/feedback" % move_base_ns, MoveBaseActionFeedback, self._feedback_cb, queue_size=10)  # type: ignore[union-attr]
        self._vel_pub = rospy.Publisher("/cmd_vel", Twist, queue_size=1)  # type: ignore[union-attr]
        self._cancel_pub = rospy.Publisher("/move_base/cancel", GoalID, queue_size=1)  # type: ignore[union-attr]
        rospy.sleep(1.0)  # type: ignore[union-attr]

    def _odom_cb(self, msg: Any) -> None:
        pass

    def _feedback_cb(self, msg: Any) -> None:
        pass

    def publish_simple_goal(
        self,
        x: float,
        y: float,
        z: float = 0.0,
        yaw: float = 0.0,
        pitch: float = 0.0,
        roll: float = 0.0,
        frame_id: str = "map",
        wait_before_pub: float = 0.5,
        qx: Optional[float] = None,
        qy: Optional[float] = None,
        qz: Optional[float] = None,
        qw: Optional[float] = None,
    ) -> bool:
        if qw is not None and qx is not None:
            yaw = yaw_from_quaternion_xyzw(qx, qy or 0.0, qz or 0.0, qw)
        ox, oy, oz, ow = _quaternion_from_yaw(yaw)
        goal = PoseStamped()  # type: ignore[misc]
        goal.header.frame_id = frame_id
        goal.header.stamp = rospy.Time.now()  # type: ignore[union-attr]
        goal.pose.position.x = x
        goal.pose.position.y = y
        goal.pose.position.z = z
        goal.pose.orientation.x = ox
        goal.pose.orientation.y = oy
        goal.pose.orientation.z = oz
        goal.pose.orientation.w = ow
        self._goal_pose = goal
        rospy.sleep(wait_before_pub)  # type: ignore[union-attr]
        self._simple_pub.publish(goal)  # type: ignore[union-attr]
        return True

    def get_current_pose(self) -> Optional[Any]:
        try:
            now = rospy.Time(0)  # type: ignore[union-attr]
            self._tf_listener.waitForTransform(  # type: ignore[union-attr]
                self._parent_frame, self._child_frame, now, rospy.Duration(10.0)  # type: ignore[union-attr]
            )
            (trans, rot) = self._tf_listener.lookupTransform(  # type: ignore[union-attr]
                self._parent_frame, self._child_frame, now
            )
        except Exception:
            return None
        pose = PoseStamped()  # type: ignore[misc]
        pose.header.frame_id = self._parent_frame
        pose.header.stamp = rospy.Time.now()  # type: ignore[union-attr]
        pose.pose.position.x = trans[0]
        pose.pose.position.y = trans[1]
        pose.pose.position.z = trans[2]
        pose.pose.orientation.x = rot[0]
        pose.pose.orientation.y = rot[1]
        pose.pose.orientation.z = rot[2]
        pose.pose.orientation.w = rot[3]
        return pose

    def nav_to_pose(self, pose: Any) -> bool:
        self._goal_pose = pose
        for i in range(3):
            self._simple_pub.publish(pose)  # type: ignore[union-attr]
            if i < 2:
                rospy.sleep(0.1)  # type: ignore[union-attr]
        return True

    def nav_to_with_yaw(
        self,
        x: float,
        y: float,
        z: float = 0.0,
        target_yaw: float = 0.0,
        frame_id: str = "map",
        reach_threshold: float = REACH_THRESHOLD,
        reach_timeout: float = 120.0,
        yaw_threshold: float = FINE_YAW_THRESHOLD,
        rotate_timeout: float = 30.0,
    ) -> bool:
        self.publish_simple_goal(x=x, y=y, z=z, yaw=0.0, frame_id=frame_id)
        self._reach_threshold = reach_threshold
        if not self.wait_until_reached(timeout_sec=reach_timeout, yaw_threshold=math.pi):
            return False
        return self.rotate_to_yaw(target_yaw, threshold=yaw_threshold, timeout_sec=rotate_timeout)

    def rotate_to_yaw(
        self,
        target_yaw: float,
        threshold: float = FINE_YAW_THRESHOLD,
        timeout_sec: float = 30.0,
        angular_speed: float = ROTATE_SPEED_DEFAULT,
    ) -> bool:
        self._cancel_pub.publish(GoalID())  # type: ignore[misc, union-attr]
        rospy.sleep(0.5)  # type: ignore[union-attr]
        self._stop()
        start = time.monotonic()
        vel = Twist()  # type: ignore[misc]
        while not rospy.is_shutdown():  # type: ignore[union-attr]
            elapsed = time.monotonic() - start
            if elapsed > timeout_sec:
                self._stop()
                return False
            current = self.get_current_pose()
            if current is None:
                time.sleep(0.1)
                continue
            current_yaw = _yaw_from_quaternion(current.pose.orientation)
            diff = _normalize_diff(target_yaw - current_yaw)
            if abs(diff) <= threshold:
                self._stop()
                return True
            omega = angular_speed if diff > 0 else -angular_speed
            if abs(diff) < 0.3:
                omega *= 0.75
            vel.angular.z = omega
            self._vel_pub.publish(vel)  # type: ignore[union-attr]
            time.sleep(0.05)
        self._stop()
        return False

    def _stop(self) -> None:
        vel = Twist()  # type: ignore[misc]
        self._vel_pub.publish(vel)  # type: ignore[union-attr]

    def _adjust_xy_to_goal(
        self, target_x: float, target_y: float, linear_speed: float = XY_FINE_SPEED,
        threshold: float = XY_FINE_THRESHOLD, timeout_sec: float = 30.0,
    ) -> bool:
        self._cancel_pub.publish(GoalID())  # type: ignore[misc, union-attr]
        rospy.sleep(0.5)  # type: ignore[union-attr]
        self._stop()
        start = time.monotonic()
        vel = Twist()  # type: ignore[misc]
        while not rospy.is_shutdown():  # type: ignore[union-attr]
            if time.monotonic() - start > timeout_sec:
                self._stop()
                return False
            current = self.get_current_pose()
            if current is None:
                time.sleep(0.1)
                continue
            dx = target_x - current.pose.position.x
            dy = target_y - current.pose.position.y
            if math.hypot(dx, dy) < threshold:
                self._stop()
                return True
            vel.linear.x = linear_speed if abs(dx) > 0.05 else 0.0
            vel.linear.y = linear_speed if abs(dy) > 0.05 else 0.0
            self._vel_pub.publish(vel)  # type: ignore[union-attr]
            time.sleep(0.05)
        self._stop()
        return False

    def wait_until_reached(
        self,
        timeout_sec: Optional[float] = None,
        yaw_threshold: float = YAW_THRESHOLD_DEFAULT,
        fine_yaw_threshold: float = FINE_YAW_THRESHOLD,
        rotate_timeout: float = 30.0,
        angular_speed: float = ROTATE_SPEED_DEFAULT,
        xy_fine_threshold: float = XY_FINE_THRESHOLD,
    ) -> bool:
        """Three-stage arrival: coarse reach -> rotate align -> XY fine adjust."""
        timeout = timeout_sec if timeout_sec is not None else 300.0
        start = time.monotonic()
        target = self._goal_pose
        if target is None:
            return False
        tx = target.pose.position.x
        ty = target.pose.position.y
        target_yaw = _yaw_from_quaternion(target.pose.orientation)
        last_yaw_diff = None
        while not rospy.is_shutdown():  # type: ignore[union-attr]
            if time.monotonic() - start > timeout:
                return False
            current = self.get_current_pose()
            if current is None:
                time.sleep(0.1)
                continue
            dist = math.hypot(current.pose.position.x - tx, current.pose.position.y - ty)
            last_yaw_diff = abs(_normalize_diff(target_yaw - _yaw_from_quaternion(current.pose.orientation)))
            if dist <= self._reach_threshold:
                break
            time.sleep(0.1)
        # Phase 2: rotate.
        if last_yaw_diff is not None and last_yaw_diff > fine_yaw_threshold:
            if not self.rotate_to_yaw(target_yaw, threshold=fine_yaw_threshold,
                                      timeout_sec=rotate_timeout, angular_speed=angular_speed):
                return False
        # Phase 3: XY fine adjust.
        current = self.get_current_pose()
        if current is not None:
            cur_dist = math.hypot(current.pose.position.x - tx, current.pose.position.y - ty)
            if cur_dist > xy_fine_threshold:
                return self._adjust_xy_to_goal(tx, ty, threshold=xy_fine_threshold)
        return True


def _normalize_diff(diff: float) -> float:
    while diff > math.pi:
        diff -= 2.0 * math.pi
    while diff < -math.pi:
        diff += 2.0 * math.pi
    return diff
