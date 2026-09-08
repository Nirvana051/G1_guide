"""ROSpy bridge for the real adapter.

All ROS work is blocking and runs in executor threads; the adapter marshals
results onto the asyncio loop. Every ROS import is guarded so this module (and
``import g1_api``) stays clean on a machine without ROS.

The navigation entry points here are *cancellable* re-implementations of the
course ``Nav2Anywhere.nav_to_with_yaw`` three-phase arrival (coarse reach ->
rotate align -> XY fine adjust): the originals poll TF until timeout and cannot
be interrupted, which would make ``DELETE :current`` hang for minutes. The
phases, thresholds and speeds are kept identical to the course values.
"""

from __future__ import annotations

import math
import subprocess
import threading
import time
from typing import Any, Dict, Optional, Tuple

__all__ = ["RosBridge"]

_TICK_S = 0.05
_POLL_S = 0.1

# Pre-rotation (planner-feedback mode): if the *global path's initial
# direction* (asked from /move_base/make_plan without executing) deviates from
# the current heading by more than TRIGGER, rotate in place until within
# ALIGNED before sending the goal. The straight-line bearing to the goal is
# only the fallback when make_plan is unavailable -- with an obstacle in
# between, the path may start in a very different direction than the goal
# bearing. Goals closer than MIN_DIST skip it entirely.
_PREROTATE_TRIGGER = 1.0    # rad, ~57 deg
_PREROTATE_ALIGNED = 0.4    # rad, ~23 deg -- "基本面朝" is enough
_PREROTATE_MIN_DIST = 0.5   # m
_PREROTATE_LOOKAHEAD = 0.5  # m of path used to measure its initial direction

# actionlib GoalStatus codes consumed in planner-feedback mode.
_STATUS_PREEMPTED = 2
_STATUS_SUCCEEDED = 3
_STATUS_ABORTED = 4


def interpret_planner_status(status: int) -> Optional[str]:
    """Map an actionlib GoalStatus code to a nav outcome, or None while the
    goal is still active. PREEMPTED surfaces as "failed" here; the caller turns
    it into "cancelled" when it was the one that cancelled."""
    if status == _STATUS_SUCCEEDED:
        return "success"
    if status == _STATUS_PREEMPTED or status >= _STATUS_ABORTED:
        # ABORTED(4)/REJECTED(5)/RECALLED(8)/LOST(9) are all terminal failures.
        return "failed"
    return None


class _MoveBaseResultListener(object):
    """Records the latest /move_base/result so nav_to can consume the planner's
    own verdict. One instance per RosBridge, subscription created lazily."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sub = None
        self._last: Optional[Tuple[float, int, str]] = None  # (t_mono, status, text)

    def ensure_subscribed(self) -> None:
        if self._sub is not None:
            return
        import rospy
        from move_base_msgs.msg import MoveBaseActionResult

        self._sub = rospy.Subscriber(
            "/move_base/result", MoveBaseActionResult, self._cb, queue_size=10
        )

    def _cb(self, msg: Any) -> None:
        with self._lock:
            self._last = (
                time.monotonic(),
                int(msg.status.status),
                str(msg.status.text or ""),
            )

    def result_since(self, t_mono: float) -> Optional[Tuple[int, str]]:
        with self._lock:
            if self._last is None or self._last[0] < t_mono:
                return None
            return self._last[1], self._last[2]


class RosBridge(object):
    def __init__(self) -> None:
        self._nav: Optional[Any] = None
        self._lock = threading.Lock()
        self._node_ready = False
        self._result_listener = _MoveBaseResultListener()

    # ------------------------------------------------------------------ node

    @staticmethod
    def available() -> bool:
        try:
            import rospy  # noqa: F401

            return True
        except Exception:
            return False

    def ensure_node(self, name: str = "g1_api") -> None:
        with self._lock:
            if self._node_ready:
                return
            import rospy

            try:
                rospy.get_rostime()
            except Exception:
                rospy.init_node(name, anonymous=False, disable_signals=True)
            self._node_ready = True

    def navigation(self) -> Any:
        self.ensure_node()
        with self._lock:
            if self._nav is None:
                from g1_api.adapters.g1.navigation_sdk import Nav2Anywhere

                self._nav = Nav2Anywhere()
            return self._nav

    # ------------------------------------------------------------------ pose

    def get_pose_tuple(self) -> Optional[Tuple[float, float, float, float]]:
        """(x, y, z, yaw) from TF map->body, or None while TF is unavailable."""
        pose = self.navigation().get_current_pose()
        if pose is None:
            return None
        from g1_api.adapters.g1.navigation_sdk import _yaw_from_quaternion

        return (
            float(pose.pose.position.x),
            float(pose.pose.position.y),
            float(pose.pose.position.z),
            _yaw_from_quaternion(pose.pose.orientation),
        )

    # ------------------------------------------------------------ navigation

    def nav_to_cancellable(
        self,
        x: float,
        y: float,
        target_yaw: float,
        with_yaw: bool,
        reach_threshold: float,
        timeout_s: float,
        cancel: threading.Event,
        yaw_threshold: float = 0.15,
        feedback: str = "tf",
    ) -> Tuple[str, str]:
        """Returns ``(outcome, reason)`` with outcome in
        {"success", "cancelled", "timeout", "failed"}.

        ``feedback="planner"`` waits on /move_base/result instead of polling TF
        distance: the planner's SUCCEEDED/ABORTED verdict decides reached-or-not
        (TF only sanity-checks afterwards). The goal then carries the real
        target yaw so move_base finishes the heading itself.
        """
        if feedback == "planner":
            return self._nav_to_planner_feedback(
                x, y, target_yaw, with_yaw, reach_threshold, timeout_s, cancel, yaw_threshold
            )
        nav = self.navigation()
        nav.publish_simple_goal(x=x, y=y, yaw=0.0)  # goal yaw 0; heading fixed after arrival
        deadline = time.monotonic() + float(timeout_s)

        # Phase 1: coarse reach, judged by TF distance (course semantics).
        while True:
            if cancel.is_set():
                self._cancel_goal_and_stop(nav)
                return "cancelled", "cancelled during transit"
            if time.monotonic() > deadline:
                self._cancel_goal_and_stop(nav)
                return "timeout", "did not reach (%.2f, %.2f) within %.0fs" % (x, y, timeout_s)
            pose = self.get_pose_tuple()
            if pose is None:
                time.sleep(_POLL_S)
                continue
            dist = math.hypot(pose[0] - x, pose[1] - y)
            if dist <= reach_threshold:
                break
            time.sleep(_POLL_S)

        # Phase 2: rotate to the requested heading.
        if with_yaw:
            outcome, reason = self.rotate_to_cancellable(target_yaw, cancel, threshold=yaw_threshold)
            if outcome != "success":
                return outcome, reason

        # Phase 3: XY fine adjust (course constants: 0.21 m/s down to 0.1 m).
        outcome, reason = self._fine_adjust_xy(x, y, cancel)
        if outcome != "success":
            return outcome, reason
        return "success", "reached target"

    def _nav_to_planner_feedback(
        self,
        x: float,
        y: float,
        target_yaw: float,
        with_yaw: bool,
        reach_threshold: float,
        timeout_s: float,
        cancel: threading.Event,
        yaw_threshold: float,
    ) -> Tuple[str, str]:
        """Layered arrival for a humanoid (AeroMaze stack).

        Layer 1, position: the planner's /move_base/result decides -- with the
        stack's yaw_goal_tolerance set to 2*pi, TEB reports SUCCEEDED the
        moment xy enters its tolerance, never fighting position and heading
        simultaneously at the goal (endgame dance / localization-slide bait on
        a walking robot). TF sanity-checks; the trim fallback targets
        reach_threshold only.

        Layer 2, heading: our slow in-place rotation to target_yaw, done when
        within yaw_threshold.

        Layer 0, pre-rotation: when the goal lies well behind the current
        heading, first rotate in place to roughly face the direction of
        travel, THEN hand over to the planner. A humanoid turning around
        mid-plan is TEB's worst case (backwards is forbidden, tight arcs near
        the deadband); facing the goal first sidesteps it entirely."""
        nav = self.navigation()
        self._result_listener.ensure_subscribed()
        deadline = time.monotonic() + float(timeout_s)

        pose = self.get_pose_tuple()
        if pose is not None:
            dx, dy = x - pose[0], y - pose[1]
            if math.hypot(dx, dy) > _PREROTATE_MIN_DIST:
                heading = self._plan_initial_direction(pose, x, y, reach_threshold)
                if heading is None:  # make_plan unavailable/failed: fall back
                    heading = math.atan2(dy, dx)
                if abs(_normalize(heading - pose[3])) > _PREROTATE_TRIGGER:
                    outcome, reason = self.rotate_to_cancellable(
                        heading, cancel, threshold=_PREROTATE_ALIGNED)
                    if outcome == "cancelled":
                        return outcome, reason
                    # timeout/failure of the pre-rotation is not fatal: fall
                    # through and let the planner try from wherever we ended.

        t_sent = time.monotonic()
        nav.publish_simple_goal(x=x, y=y, yaw=target_yaw if with_yaw else 0.0)

        while True:
            if cancel.is_set():
                self._cancel_goal_and_stop(nav)
                return "cancelled", "cancelled during transit"
            if time.monotonic() > deadline:
                self._cancel_goal_and_stop(nav)
                return "timeout", "planner returned no result for (%.2f, %.2f) within %.0fs" % (
                    x, y, timeout_s)
            result = self._result_listener.result_since(t_sent)
            if result is None:
                time.sleep(_POLL_S)
                continue
            status, text = result
            verdict = interpret_planner_status(status)
            if verdict is None:  # PENDING/ACTIVE preludes; keep waiting
                time.sleep(_POLL_S)
                continue
            if verdict != "success":
                self._cancel_goal_and_stop(nav)
                return "failed", "planner reported goal %s (status %d): %s" % (
                    "preempted" if status == _STATUS_PREEMPTED else "aborted", status,
                    text or "no reason given")
            break

        # Planner says reached; verify against the API's own thresholds. The
        # fallback trims only to reach_threshold -- not the course 0.1 m -- so
        # a loosened planner tolerance is not silently re-tightened here.
        pose = self.get_pose_tuple()
        if pose is not None and math.hypot(pose[0] - x, pose[1] - y) > reach_threshold:
            outcome, reason = self._fine_adjust_xy(
                x, y, cancel, threshold=max(0.1, float(reach_threshold))
            )
            if outcome != "success":
                return outcome, reason
        if with_yaw:
            pose = self.get_pose_tuple()
            if pose is not None and abs(_normalize(target_yaw - pose[3])) > yaw_threshold:
                outcome, reason = self.rotate_to_cancellable(target_yaw, cancel, threshold=yaw_threshold)
                if outcome != "success":
                    return outcome, reason
                return "success", "planner reached position; heading aligned"
        return "success", "planner reported goal reached"

    def _plan_initial_direction(
        self,
        pose: Tuple[float, float, float, float],
        gx: float,
        gy: float,
        tolerance: float,
        timeout_s: float = 2.0,
    ) -> Optional[float]:
        """Initial direction (rad, map frame) of the global path from the
        current pose to (gx, gy), computed via /move_base/make_plan WITHOUT
        executing anything. Returns None when the service is unavailable or
        planning fails -- callers fall back to the straight-line bearing."""
        try:
            import rospy
            from geometry_msgs.msg import PoseStamped
            from nav_msgs.srv import GetPlan

            def _stamped(px: float, py: float, yaw: float) -> Any:
                msg = PoseStamped()
                msg.header.frame_id = "map"
                msg.header.stamp = rospy.Time(0)
                msg.pose.position.x = px
                msg.pose.position.y = py
                msg.pose.orientation.z = math.sin(yaw / 2.0)
                msg.pose.orientation.w = math.cos(yaw / 2.0)
                return msg

            rospy.wait_for_service("/move_base/make_plan", timeout=timeout_s)
            proxy = rospy.ServiceProxy("/move_base/make_plan", GetPlan)
            resp = proxy(_stamped(pose[0], pose[1], pose[3]),
                         _stamped(gx, gy, 0.0), float(tolerance))
            poses = list(resp.plan.poses)
            if len(poses) < 2:
                return None
            x0 = float(poses[0].pose.position.x)
            y0 = float(poses[0].pose.position.y)
            last_dx = last_dy = 0.0
            for p in poses[1:]:
                dx = float(p.pose.position.x) - x0
                dy = float(p.pose.position.y) - y0
                if math.hypot(dx, dy) >= _PREROTATE_LOOKAHEAD:
                    return math.atan2(dy, dx)
                last_dx, last_dy = dx, dy
            # path shorter than the lookahead: use its overall direction
            if math.hypot(last_dx, last_dy) > 0.05:
                return math.atan2(last_dy, last_dx)
            return None
        except Exception:  # noqa: BLE001 -- any failure means "no path info"
            return None

    def rotate_to_cancellable(
        self,
        target_yaw: float,
        cancel: threading.Event,
        threshold: float = 0.15,
        timeout_s: float = 30.0,
        angular_speed: float = 0.4133,
    ) -> Tuple[str, str]:
        nav = self.navigation()
        self._cancel_goal_and_stop(nav)
        import rospy
        from geometry_msgs.msg import Twist

        deadline = time.monotonic() + float(timeout_s)
        vel = Twist()
        while not rospy.is_shutdown():
            if cancel.is_set():
                nav._stop()
                return "cancelled", "cancelled during rotation"
            if time.monotonic() > deadline:
                nav._stop()
                return "timeout", "rotation to %.2f rad timed out" % (target_yaw,)
            pose = self.get_pose_tuple()
            if pose is None:
                time.sleep(_POLL_S)
                continue
            diff = _normalize(target_yaw - pose[3])
            if abs(diff) <= threshold:
                nav._stop()
                return "success", "heading reached"
            omega = angular_speed if diff > 0 else -angular_speed
            if abs(diff) < 0.3:
                omega *= 0.75
            vel.angular.z = omega
            nav._vel_pub.publish(vel)
            time.sleep(_TICK_S)
        nav._stop()
        return "failed", "rospy shut down"

    def _fine_adjust_xy(
        self,
        target_x: float,
        target_y: float,
        cancel: threading.Event,
        linear_speed: float = 0.21,
        threshold: float = 0.1,
        timeout_s: float = 30.0,
    ) -> Tuple[str, str]:
        nav = self.navigation()
        pose = self.get_pose_tuple()
        if pose is not None and math.hypot(pose[0] - target_x, pose[1] - target_y) <= threshold:
            return "success", "already within fine threshold"
        import rospy
        from geometry_msgs.msg import Twist

        deadline = time.monotonic() + float(timeout_s)
        vel = Twist()
        while not rospy.is_shutdown():
            if cancel.is_set():
                nav._stop()
                return "cancelled", "cancelled during fine adjust"
            if time.monotonic() > deadline:
                nav._stop()
                return "success", "fine adjust timed out; within coarse threshold"
            pose = self.get_pose_tuple()
            if pose is None:
                time.sleep(_POLL_S)
                continue
            dx = target_x - pose[0]
            dy = target_y - pose[1]
            if math.hypot(dx, dy) < threshold:
                nav._stop()
                return "success", "fine position reached"
            # Course behaviour: world-frame deltas driven with body-frame speeds.
            # Project the delta into the body frame using the current yaw.
            yaw = pose[3]
            bx = math.cos(yaw) * dx + math.sin(yaw) * dy
            by = -math.sin(yaw) * dx + math.cos(yaw) * dy
            vel.linear.x = linear_speed * (1 if bx > 0 else -1) if abs(bx) > 0.05 else 0.0
            vel.linear.y = linear_speed * (1 if by > 0 else -1) if abs(by) > 0.05 else 0.0
            nav._vel_pub.publish(vel)
            time.sleep(_TICK_S)
        nav._stop()
        return "failed", "rospy shut down"

    def _cancel_goal_and_stop(self, nav: Any) -> None:
        from actionlib_msgs.msg import GoalID

        nav._cancel_pub.publish(GoalID())
        nav._stop()

    def cmd_vel_pulse(
        self, vx: float, vy: float, omega: float, duration_s: float, cancel: threading.Event
    ) -> bool:
        """Publish a constant Twist for ``duration_s`` then zeros (MoveAction on
        the /cmd_vel path -- what LocoClient.Move + StopMove do via the bridge).
        Returns False when cancelled."""
        nav = self.navigation()
        from geometry_msgs.msg import Twist

        vel = Twist()
        vel.linear.x, vel.linear.y, vel.angular.z = float(vx), float(vy), float(omega)
        deadline = time.monotonic() + float(duration_s)
        cancelled = False
        while time.monotonic() < deadline:
            if cancel.is_set():
                cancelled = True
                break
            nav._vel_pub.publish(vel)
            time.sleep(_TICK_S)
        stop = Twist()
        for _ in range(5):
            nav._vel_pub.publish(stop)
            time.sleep(0.02)
        return not cancelled

    def stop(self) -> None:
        if self._nav is not None:
            self._cancel_goal_and_stop(self._nav)

    def clear_costmaps(self) -> None:
        self.ensure_node()
        import rospy
        from std_srvs.srv import Empty

        try:
            proxy = rospy.ServiceProxy("/move_base/clear_costmaps", Empty)
            proxy()
        except Exception:
            pass

    # ---------------------------------------------------------- relocalization

    def call_slam_reloc(
        self, pcd_path: str, x: float, y: float, yaw: float, timeout_s: float = 30.0
    ) -> Tuple[bool, str]:
        self.ensure_node()
        import rospy

        try:
            from fastlio.srv import SlamReLoc

            rospy.wait_for_service("/slam_reloc", timeout=timeout_s)
            proxy = rospy.ServiceProxy("/slam_reloc", SlamReLoc)
            resp = proxy(pcd_path=pcd_path, x=float(x), y=float(y), z=0.0,
                         roll=0.0, pitch=0.0, yaw=float(yaw))
            return True, "status=%s %s" % (getattr(resp, "status", "?"), getattr(resp, "message", ""))
        except ImportError:
            # fastlio srv python bindings absent: fall back to the CLI.
            cmd = [
                "rosservice", "call", "/slam_reloc",
                "{pcd_path: '%s', x: %f, y: %f, z: 0.0, roll: 0.0, pitch: 0.0, yaw: %f}"
                % (pcd_path, float(x), float(y), float(yaw)),
            ]
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
                ok = out.returncode == 0
                return ok, (out.stdout or out.stderr or b"").decode(errors="replace").strip()
            except Exception as exc:  # noqa: BLE001
                return False, "rosservice call failed: %s" % (exc,)
        except Exception as exc:  # noqa: BLE001
            return False, "/slam_reloc failed: %s" % (exc,)

    def wait_reloc_confirmed(self, timeout_s: float = 20.0) -> bool:
        self.ensure_node()
        import rospy

        deadline = time.monotonic() + float(timeout_s)
        try:
            from fastlio.srv import SlamRelocCheck

            proxy = rospy.ServiceProxy("/slam_reloc_check", SlamRelocCheck)
            while time.monotonic() < deadline:
                try:
                    resp = proxy(True)
                    if bool(getattr(resp, "status", False)):
                        return True
                except Exception:
                    pass
                time.sleep(0.5)
            return False
        except ImportError:
            # No bindings: give the localizer a fixed settle window.
            time.sleep(min(3.0, timeout_s))
            return True

    # ---------------------------------------------------------------- health

    def _system_state(self) -> Optional[Tuple[Any, Any, Any]]:
        try:
            import rosgraph

            master = rosgraph.Master("/g1_api_health")
            return master.getSystemState()
        except Exception:
            return None

    @staticmethod
    def _uri_alive(uri: str, timeout: float = 1.0) -> bool:
        """TCP-connect check: master registrations outlive dead nodes, so a
        lookup alone can report a process that is long gone."""
        import socket
        from urllib.parse import urlparse

        try:
            parsed = urlparse(uri if "//" in uri else "//" + uri)
            host, port = parsed.hostname, parsed.port
            if host is None or port is None:
                return False
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _move_base_registered(self) -> bool:
        """Is the /move_base node registered AND alive?

        Deliberately NOT the /move_base/status topic: move_base blocks inside
        its costmap constructor until the map->base_link TF exists, which on
        this stack only happens AFTER relocalization -- the status topic
        appearing is a post-reloc signal, node liveness is the health one.
        """
        try:
            import rosgraph

            master = rosgraph.Master("/g1_api_health")
            return self._uri_alive(master.lookupNode("/move_base"))
        except Exception:
            return False

    def _service_alive(self, name: str) -> bool:
        try:
            import rosgraph

            master = rosgraph.Master("/g1_api_health")
            uri = master.lookupService(name)
            # rosrpc://host:port
            return self._uri_alive(uri.replace("rosrpc://", "http://"))
        except Exception:
            return False

    def ros_health(self) -> Dict[str, bool]:
        """roscore/move_base/velocity-bridge liveness via the master API."""
        state = self._system_state()
        if state is None:
            return {"roscore_down": True, "move_base_down": True, "velocity_bridge_down": True}
        _pubs, subs, _srvs = state
        cmd_vel_subs = [nodes for name, nodes in subs if name == "/cmd_vel"]
        return {
            "roscore_down": False,
            "move_base_down": not self._move_base_registered(),
            "velocity_bridge_down": not (cmd_vel_subs and cmd_vel_subs[0]),
        }

    def node_alive(self, name: str) -> bool:
        """Registered with the master AND answering a TCP connect."""
        try:
            import rosgraph

            master = rosgraph.Master("/g1_api_health")
            return self._uri_alive(master.lookupNode(name))
        except Exception:
            return False

    def wait_node_alive(self, name: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + float(timeout_s)
        while time.monotonic() < deadline:
            if self.node_alive(name):
                return True
            time.sleep(1.0)
        return False

    def wait_nav_ready(self, timeout_s: float) -> bool:
        """After a launch (re)start: wait until the /slam_reloc service and the
        /move_base node are registered AND answer a TCP connect (stale
        registrations from a previous stack must not satisfy the gate)."""
        deadline = time.monotonic() + float(timeout_s)
        while time.monotonic() < deadline:
            if self._service_alive("/slam_reloc") and self._move_base_registered():
                return True
            time.sleep(1.0)
        return False


def _normalize(diff: float) -> float:
    while diff > math.pi:
        diff -= 2.0 * math.pi
    while diff < -math.pi:
        diff += 2.0 * math.pi
    return diff
