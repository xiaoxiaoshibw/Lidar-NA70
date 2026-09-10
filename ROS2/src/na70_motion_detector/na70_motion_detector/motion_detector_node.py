"""ROS2 wrapper for the minimal NA70 motion-presence detector."""

from typing import Iterable

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2, PointField

# WSL2 mirrored 模式下 lo 的 UDP 大包(>1KB)被静默丢弃，BEST_EFFORT
# 分片无法重组。PointCloud2 输入/输出必须用 RELIABLE 才能跨进程送达。
CLOUD_QOS = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Bool, Float32, UInt32
from std_srvs.srv import Trigger

from .motion_core import MotionDetectorConfig, VoxelMotionDetector


class MotionDetectorNode(Node):
    def __init__(self) -> None:
        super().__init__("na70_motion_detector")

        self.declare_parameter("input_topic", "/inno_lidar_cloud")
        self.declare_parameter("moving_points_topic", "/motion_detector/moving_points")
        self.declare_parameter("has_motion_topic", "/motion_detector/has_motion")
        self.declare_parameter("voxel_size", 0.15)
        self.declare_parameter("warmup_frames", 100)
        self.declare_parameter("background_min_hits", 3)
        self.declare_parameter("background_margin_voxels", 1)
        self.declare_parameter("min_range", 0.5)
        self.declare_parameter("max_range", 100.0)
        self.declare_parameter("min_z", -1000.0)
        self.declare_parameter("max_z", 1000.0)
        self.declare_parameter("min_moving_points", 30)
        self.declare_parameter("min_moving_ratio", 0.001)
        self.declare_parameter("confirmation_frames", 2)
        self.declare_parameter("clear_frames", 3)

        config = MotionDetectorConfig(
            voxel_size=float(self.get_parameter("voxel_size").value),
            warmup_frames=int(self.get_parameter("warmup_frames").value),
            background_min_hits=int(self.get_parameter("background_min_hits").value),
            background_margin_voxels=int(
                self.get_parameter("background_margin_voxels").value
            ),
            min_range=float(self.get_parameter("min_range").value),
            max_range=float(self.get_parameter("max_range").value),
            min_z=float(self.get_parameter("min_z").value),
            max_z=float(self.get_parameter("max_z").value),
            min_moving_points=int(self.get_parameter("min_moving_points").value),
            min_moving_ratio=float(self.get_parameter("min_moving_ratio").value),
            confirmation_frames=int(self.get_parameter("confirmation_frames").value),
            clear_frames=int(self.get_parameter("clear_frames").value),
        )
        self.detector = VoxelMotionDetector(config)

        input_topic = str(self.get_parameter("input_topic").value)
        moving_topic = str(self.get_parameter("moving_points_topic").value)
        motion_topic = str(self.get_parameter("has_motion_topic").value)

        self.motion_pub = self.create_publisher(Bool, motion_topic, 10)
        self.count_pub = self.create_publisher(UInt32, "/motion_detector/moving_count", 10)
        self.ratio_pub = self.create_publisher(Float32, "/motion_detector/moving_ratio", 10)
        self.points_pub = self.create_publisher(
            PointCloud2, moving_topic, CLOUD_QOS
        )
        self.subscription = self.create_subscription(
            PointCloud2, input_topic, self._cloud_callback, CLOUD_QOS
        )
        self.reset_service = self.create_service(
            Trigger, "/motion_detector/reset_background", self._reset_background
        )
        self._last_motion = None

        self.get_logger().info(
            f"Listening on {input_topic}; keep the scene empty for "
            f"{config.warmup_frames} warmup frames"
        )

    @staticmethod
    def _read_xyzi(message: PointCloud2) -> np.ndarray:
        field_names = {field.name for field in message.fields}
        required = {"x", "y", "z"}
        if not required.issubset(field_names):
            raise ValueError(f"PointCloud2 is missing fields: {sorted(required - field_names)}")

        names = ("x", "y", "z", "intensity") if "intensity" in field_names else ("x", "y", "z")
        if hasattr(point_cloud2, "read_points_numpy"):
            points = point_cloud2.read_points_numpy(
                message, field_names=names, skip_nans=False
            )
            points = np.asarray(points, dtype=np.float32).reshape(-1, len(names))
        else:
            rows: Iterable = point_cloud2.read_points(
                message, field_names=names, skip_nans=False
            )
            points = np.asarray(list(rows), dtype=np.float32).reshape(-1, len(names))

        if len(names) == 3:
            points = np.column_stack(
                [points, np.zeros(len(points), dtype=np.float32)]
            )
        return points

    @staticmethod
    def _create_cloud(message: PointCloud2, points: np.ndarray) -> PointCloud2:
        fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
            PointField(name="intensity", offset=12, datatype=PointField.FLOAT32, count=1),
        ]
        return point_cloud2.create_cloud(message.header, fields, points[:, :4].tolist())

    def _cloud_callback(self, message: PointCloud2) -> None:
        try:
            points = self._read_xyzi(message)
            result = self.detector.process(points)
        except (ValueError, TypeError) as exc:
            self.get_logger().error(f"Rejected point cloud: {exc}")
            return

        self.motion_pub.publish(Bool(data=result.has_motion))
        self.count_pub.publish(UInt32(data=result.moving_count))
        self.ratio_pub.publish(Float32(data=float(result.moving_ratio)))
        self.points_pub.publish(self._create_cloud(message, points[result.moving_mask]))

        if not result.ready:
            self.get_logger().info(
                f"Background warmup {result.warmup_frames_seen}/"
                f"{self.detector.config.warmup_frames}",
                throttle_duration_sec=2.0,
            )
        elif self._last_motion is None or self._last_motion != result.has_motion:
            state = "MOTION" if result.has_motion else "CLEAR"
            self.get_logger().info(
                f"State={state}, moving_points={result.moving_count}, "
                f"ratio={result.moving_ratio:.4f}"
            )
        self._last_motion = result.has_motion

    def _reset_background(self, _request: Trigger.Request, response: Trigger.Response):
        self.detector.reset()
        self._last_motion = None
        response.success = True
        response.message = "Background cleared; keep the scene empty during warmup"
        return response


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionDetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

