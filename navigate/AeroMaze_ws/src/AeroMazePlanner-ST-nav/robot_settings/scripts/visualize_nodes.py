#!/usr/bin/env python3

import json
import math
import os

import rospkg
import rospy
from geometry_msgs.msg import Point
from geometry_msgs.msg import Quaternion
from visualization_msgs.msg import Marker
from visualization_msgs.msg import MarkerArray


class NodesVisualizer:
    def __init__(self):
        pkg_path = rospkg.RosPack().get_path("robot_settings")
        default_nodes_file = os.path.join(pkg_path, "paths", "nodes.json")

        self.frame_id = rospy.get_param("~frame_id", "map")
        self.nodes_file = rospy.get_param("~nodes_file", default_nodes_file)
        self.marker_topic = rospy.get_param("~marker_topic", "nodes_markers")
        self.publish_rate = rospy.get_param("~publish_rate", 1.0)

        self.point_scale = rospy.get_param("~point_scale", 1.2)
        self.text_scale = rospy.get_param("~text_scale", 1.8)
        self.arrow_length = rospy.get_param("~arrow_length", 2.0)
        self.arrow_width = rospy.get_param("~arrow_width", 0.28)
        self.line_width = rospy.get_param("~line_width", 0.18)
        self.z_offset = rospy.get_param("~z_offset", 0.4)

        self.show_origin_marker = rospy.get_param("~show_origin_marker", True)
        self.origin_marker_scale = rospy.get_param("~origin_marker_scale", 2.0)
        self.show_center_marker = rospy.get_param("~show_center_marker", True)
        self.center_marker_scale = rospy.get_param("~center_marker_scale", 2.2)

        self.publisher = rospy.Publisher(self.marker_topic, MarkerArray, queue_size=1, latch=True)
        self.points_2d = rospy.get_param("~points_2d", [])
        self.closed_loop = rospy.get_param("~closed_loop", False)
        self.default_yaw = rospy.get_param("~default_yaw", 0.0)
        self.use_json_fallback = rospy.get_param("~use_json_fallback", False)
        self.text_yaw_offset = rospy.get_param("~text_yaw_offset", 0.0)

        if self.points_2d:
            self.nodes = self._load_nodes_from_points(self.points_2d, self.closed_loop)
            rospy.loginfo("nodes_visualizer: loaded %d custom 2D points from ~points_2d", len(self.nodes))
        elif self.use_json_fallback:
            self.nodes = self._load_nodes(self.nodes_file)
            rospy.loginfo("nodes_visualizer: loaded %d nodes from json file %s", len(self.nodes), self.nodes_file)
        else:
            self.nodes = {}
            rospy.logwarn("nodes_visualizer: ~points_2d is empty, no nodes to display (json fallback disabled)")

    @staticmethod
    def _sort_key(node_id):
        return int(node_id) if str(node_id).isdigit() else str(node_id)

    def _load_nodes(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError("nodes file does not exist: {}".format(file_path))

        with open(file_path, "r", encoding="utf-8") as file_obj:
            raw = json.load(file_obj)

        nodes = {}
        for node_id, node_data in raw.items():
            pose = node_data["pose"]
            position = pose["position"]
            orientation = pose["orientation"]
            nodes[str(node_id)] = {
                "position": {
                    "x": float(position["x"]),
                    "y": float(position["y"]),
                    "z": float(position.get("z", 0.0)),
                },
                "orientation": {
                    "x": float(orientation["x"]),
                    "y": float(orientation["y"]),
                    "z": float(orientation["z"]),
                    "w": float(orientation["w"]),
                },
                "neighbors": [str(nei) for nei in node_data.get("neighbors", [])],
            }
        return nodes

    @staticmethod
    def _yaw_to_quaternion(yaw):
        half = yaw * 0.5
        return {
            "x": 0.0,
            "y": 0.0,
            "z": math.sin(half),
            "w": math.cos(half),
        }

    @staticmethod
    def _dict_to_quaternion(q_dict):
        quat = Quaternion()
        quat.x = q_dict["x"]
        quat.y = q_dict["y"]
        quat.z = q_dict["z"]
        quat.w = q_dict["w"]
        return quat

    @staticmethod
    def _parse_point(point):
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x = float(point[0])
            y = float(point[1])
            z = float(point[2]) if len(point) >= 3 else 0.0
            return {"x": x, "y": y, "z": z}

        if isinstance(point, dict) and ("x" in point) and ("y" in point):
            return {
                "x": float(point["x"]),
                "y": float(point["y"]),
                "z": float(point.get("z", 0.0)),
            }

        raise ValueError("point must be [x, y] or {x:..., y:...}, got: {}".format(point))

    def _load_nodes_from_points(self, points_2d, closed_loop):
        positions = [self._parse_point(p) for p in points_2d]
        if not positions:
            return {}

        nodes = {}
        count = len(positions)
        for idx in range(count):
            current = positions[idx]
            node_id = str(idx + 1)

            yaw_override = None
            if isinstance(points_2d[idx], dict) and ("yaw" in points_2d[idx]):
                yaw_override = float(points_2d[idx]["yaw"])

            if yaw_override is not None:
                yaw = yaw_override
            elif count == 1:
                yaw = self.default_yaw
            elif idx < count - 1:
                nxt = positions[idx + 1]
                yaw = math.atan2(nxt["y"] - current["y"], nxt["x"] - current["x"])
            elif closed_loop:
                nxt = positions[0]
                yaw = math.atan2(nxt["y"] - current["y"], nxt["x"] - current["x"])
            else:
                prev = positions[idx - 1]
                yaw = math.atan2(current["y"] - prev["y"], current["x"] - prev["x"])

            neighbors = []
            if idx < count - 1:
                neighbors.append(str(idx + 2))
            elif closed_loop and count > 1:
                neighbors.append("1")

            nodes[node_id] = {
                "position": current,
                "orientation": self._yaw_to_quaternion(yaw),
                "neighbors": neighbors,
            }

        return nodes

    def _new_marker(self, ns, marker_id, marker_type, stamp):
        marker = Marker()
        marker.header.frame_id = self.frame_id
        marker.header.stamp = stamp
        marker.ns = ns
        marker.id = marker_id
        marker.type = marker_type
        marker.action = Marker.ADD
        marker.pose.orientation.w = 1.0
        return marker

    def _build_markers(self):
        stamp = rospy.Time.now()
        markers = MarkerArray()

        clear_marker = Marker()
        clear_marker.header.frame_id = self.frame_id
        clear_marker.header.stamp = stamp
        clear_marker.action = Marker.DELETEALL
        markers.markers.append(clear_marker)

        if self.show_origin_marker:
            origin_marker = self._new_marker("nodes_debug", 10000, Marker.SPHERE, stamp)
            origin_marker.pose.position.x = 0.0
            origin_marker.pose.position.y = 0.0
            origin_marker.pose.position.z = self.z_offset
            origin_marker.scale.x = self.origin_marker_scale
            origin_marker.scale.y = self.origin_marker_scale
            origin_marker.scale.z = self.origin_marker_scale
            origin_marker.color.r = 0.10
            origin_marker.color.g = 1.00
            origin_marker.color.b = 0.10
            origin_marker.color.a = 0.95
            markers.markers.append(origin_marker)

            origin_text = self._new_marker("nodes_debug", 10001, Marker.TEXT_VIEW_FACING, stamp)
            origin_text.pose.position.x = 0.0
            origin_text.pose.position.y = 0.0
            origin_text.pose.position.z = self.z_offset + 1.5
            origin_text.scale.z = 1.2
            origin_text.color.r = 0.10
            origin_text.color.g = 1.00
            origin_text.color.b = 0.10
            origin_text.color.a = 1.00
            origin_text.text = "ORIGIN(0,0)"
            markers.markers.append(origin_text)

        if self.show_center_marker and self.nodes:
            xs = [node["position"]["x"] for node in self.nodes.values()]
            ys = [node["position"]["y"] for node in self.nodes.values()]
            center_x = sum(xs) / len(xs)
            center_y = sum(ys) / len(ys)

            center_marker = self._new_marker("nodes_debug", 10002, Marker.CUBE, stamp)
            center_marker.pose.position.x = center_x
            center_marker.pose.position.y = center_y
            center_marker.pose.position.z = self.z_offset
            center_marker.scale.x = self.center_marker_scale
            center_marker.scale.y = self.center_marker_scale
            center_marker.scale.z = self.center_marker_scale
            center_marker.color.r = 1.00
            center_marker.color.g = 0.10
            center_marker.color.b = 1.00
            center_marker.color.a = 0.90
            markers.markers.append(center_marker)

            center_text = self._new_marker("nodes_debug", 10003, Marker.TEXT_VIEW_FACING, stamp)
            center_text.pose.position.x = center_x
            center_text.pose.position.y = center_y
            center_text.pose.position.z = self.z_offset + 1.7
            center_text.scale.z = 1.2
            center_text.color.r = 1.00
            center_text.color.g = 0.20
            center_text.color.b = 1.00
            center_text.color.a = 1.00
            center_text.text = "NODES_CENTER"
            markers.markers.append(center_text)

        edge_marker = self._new_marker("nodes_edges", 0, Marker.LINE_LIST, stamp)
        edge_marker.scale.x = self.line_width
        edge_marker.color.r = 0.20
        edge_marker.color.g = 0.70
        edge_marker.color.b = 1.00
        edge_marker.color.a = 0.85

        edge_seen = set()
        for node_id in sorted(self.nodes.keys(), key=self._sort_key):
            node = self.nodes[node_id]
            start = node["position"]
            for neighbor_id in node["neighbors"]:
                if neighbor_id not in self.nodes:
                    rospy.logwarn_throttle(5.0, "neighbor %s referenced by %s is missing", neighbor_id, node_id)
                    continue
                edge_key = tuple(sorted((node_id, neighbor_id), key=self._sort_key))
                if edge_key in edge_seen:
                    continue
                edge_seen.add(edge_key)

                end = self.nodes[neighbor_id]["position"]
                p_start = Point(x=start["x"], y=start["y"], z=start["z"] + self.z_offset)
                p_end = Point(x=end["x"], y=end["y"], z=end["z"] + self.z_offset)
                edge_marker.points.extend([p_start, p_end])

        markers.markers.append(edge_marker)

        ordered_ids = sorted(self.nodes.keys(), key=self._sort_key)
        for idx, node_id in enumerate(ordered_ids):
            node = self.nodes[node_id]
            position = node["position"]
            orientation = node["orientation"]

            point_marker = self._new_marker("nodes_points", idx, Marker.SPHERE, stamp)
            point_marker.pose.position.x = position["x"]
            point_marker.pose.position.y = position["y"]
            point_marker.pose.position.z = position["z"] + self.z_offset
            point_marker.scale.x = self.point_scale
            point_marker.scale.y = self.point_scale
            point_marker.scale.z = self.point_scale
            point_marker.color.r = 1.00
            point_marker.color.g = 0.35
            point_marker.color.b = 0.15
            point_marker.color.a = 0.95
            markers.markers.append(point_marker)

            arrow_marker = self._new_marker("nodes_heading", idx, Marker.ARROW, stamp)
            arrow_marker.pose.position.x = position["x"]
            arrow_marker.pose.position.y = position["y"]
            arrow_marker.pose.position.z = position["z"] + self.z_offset + 0.08
            arrow_marker.pose.orientation.x = orientation["x"]
            arrow_marker.pose.orientation.y = orientation["y"]
            arrow_marker.pose.orientation.z = orientation["z"]
            arrow_marker.pose.orientation.w = orientation["w"]
            arrow_marker.scale.x = self.arrow_length
            arrow_marker.scale.y = self.arrow_width
            arrow_marker.scale.z = self.arrow_width
            arrow_marker.color.r = 1.00
            arrow_marker.color.g = 1.00
            arrow_marker.color.b = 0.10
            arrow_marker.color.a = 0.90
            markers.markers.append(arrow_marker)

            text_marker = self._new_marker("nodes_labels", idx, Marker.TEXT_VIEW_FACING, stamp)
            text_marker.pose.position.x = position["x"]
            text_marker.pose.position.y = position["y"]
            text_marker.pose.position.z = position["z"] + self.z_offset + 1.4
            text_marker.scale.z = self.text_scale
            text_marker.color.r = 1.00
            text_marker.color.g = 1.00
            text_marker.color.b = 1.00
            text_marker.color.a = 1.00
            text_marker.text = str(node_id)
            markers.markers.append(text_marker)

        return markers

    def spin(self):
        rate = rospy.Rate(self.publish_rate) if self.publish_rate > 0.0 else None
        while not rospy.is_shutdown():
            marker_array = self._build_markers()
            self.publisher.publish(marker_array)
            if rate is None:
                rospy.sleep(1.0)
            else:
                rate.sleep()


def main():
    rospy.init_node("nodes_visualizer")
    try:
        visualizer = NodesVisualizer()
    except Exception as exc:  # pylint: disable=broad-except
        rospy.logerr("failed to initialize nodes visualizer: %s", exc)
        raise
    visualizer.spin()


if __name__ == "__main__":
    main()
