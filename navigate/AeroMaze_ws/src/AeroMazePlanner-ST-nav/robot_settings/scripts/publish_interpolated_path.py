#!/usr/bin/env python3

import math

import rospy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Path


class InterpolatedPathPublisher:
    def __init__(self):
        self.frame_id = rospy.get_param("~frame_id", "map")
        self.path_topic = rospy.get_param("~path_topic", "/path_boundry_path")
        self.interp_step = float(rospy.get_param("~interp_step", 0.2))
        self.publish_rate = float(rospy.get_param("~publish_rate", 1.0))
        self.points_2d = rospy.get_param("~points_2d", [])

        if self.interp_step <= 0.0:
            rospy.logwarn("interp_step <= 0, force to 0.2m")
            self.interp_step = 0.2

        self.path_pub = rospy.Publisher(self.path_topic, Path, queue_size=1, latch=True)
        self.path_msg = self._build_path_msg()

        rospy.loginfo(
            "publish_interpolated_path: frame=%s topic=%s raw_points=%d interpolated_points=%d step=%.3f",
            self.frame_id,
            self.path_topic,
            len(self.points_2d),
            len(self.path_msg.poses),
            self.interp_step,
        )

    @staticmethod
    def _parse_point(point):
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x = float(point[0])
            y = float(point[1])
            z = float(point[2]) if len(point) >= 3 else 0.0
            return (x, y, z)

        if isinstance(point, dict) and ("x" in point) and ("y" in point):
            x = float(point["x"])
            y = float(point["y"])
            z = float(point.get("z", 0.0))
            return (x, y, z)

        raise ValueError("invalid point format: {}".format(point))

    @staticmethod
    def _yaw_to_quaternion(yaw):
        half = yaw * 0.5
        qx = 0.0
        qy = 0.0
        qz = math.sin(half)
        qw = math.cos(half)
        return (qx, qy, qz, qw)

    def _interpolate_polyline(self, points):
        if not points:
            return []

        if len(points) == 1:
            return [points[0]]

        interpolated = [points[0]]
        for idx in range(len(points) - 1):
            x0, y0, z0 = points[idx]
            x1, y1, z1 = points[idx + 1]
            dx = x1 - x0
            dy = y1 - y0
            dz = z1 - z0
            dist = math.hypot(dx, dy)

            if dist < 1e-9:
                continue

            steps = max(1, int(math.ceil(dist / self.interp_step)))
            for step_idx in range(1, steps + 1):
                t = float(step_idx) / float(steps)
                interpolated.append((x0 + dx * t, y0 + dy * t, z0 + dz * t))

        return interpolated

    def _build_path_msg(self):
        path_msg = Path()
        path_msg.header.frame_id = self.frame_id

        if not self.points_2d:
            rospy.logwarn("publish_interpolated_path: ~points_2d is empty, publish empty Path")
            return path_msg

        raw_points = [self._parse_point(p) for p in self.points_2d]
        points = self._interpolate_polyline(raw_points)

        if not points:
            rospy.logwarn("publish_interpolated_path: no valid points after interpolation")
            return path_msg

        stamp = rospy.Time.now()
        path_msg.header.stamp = stamp

        count = len(points)
        for idx, (x, y, z) in enumerate(points):
            pose = PoseStamped()
            pose.header.frame_id = self.frame_id
            pose.header.stamp = stamp
            pose.pose.position.x = x
            pose.pose.position.y = y
            pose.pose.position.z = z

            if count == 1:
                yaw = 0.0
            elif idx < count - 1:
                nx, ny, _ = points[idx + 1]
                yaw = math.atan2(ny - y, nx - x)
            else:
                px, py, _ = points[idx - 1]
                yaw = math.atan2(y - py, x - px)

            qx, qy, qz, qw = self._yaw_to_quaternion(yaw)
            pose.pose.orientation.x = qx
            pose.pose.orientation.y = qy
            pose.pose.orientation.z = qz
            pose.pose.orientation.w = qw
            path_msg.poses.append(pose)

        return path_msg

    def spin(self):
        rate = rospy.Rate(self.publish_rate) if self.publish_rate > 0.0 else None

        while not rospy.is_shutdown():
            now = rospy.Time.now()
            self.path_msg.header.stamp = now
            for pose in self.path_msg.poses:
                pose.header.stamp = now
            self.path_pub.publish(self.path_msg)

            if rate is None:
                rospy.sleep(1.0)
            else:
                rate.sleep()


def main():
    rospy.init_node("publish_interpolated_path")
    try:
        publisher = InterpolatedPathPublisher()
    except Exception as exc:  # pylint: disable=broad-except
        rospy.logerr("publish_interpolated_path init failed: %s", exc)
        raise
    publisher.spin()


if __name__ == "__main__":
    main()
