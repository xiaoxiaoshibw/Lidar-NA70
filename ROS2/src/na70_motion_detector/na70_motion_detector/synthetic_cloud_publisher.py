"""Publish a short synthetic PointCloud2 sequence for ROS2 smoke testing."""

import argparse
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2, PointField

# WSL2 mirrored 模式下 lo 的 UDP 大包(>1KB)被静默丢弃，BEST_EFFORT
# 分片无法重组。PointCloud2 ~11KB 必须用 RELIABLE 才能跨进程送达。
CLOUD_QOS = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
from sensor_msgs_py import point_cloud2


def make_frames(warmup_frames: int, moving_frames: int, clear_frames: int):
    x, y = np.meshgrid(np.linspace(-3.0, 3.0, 25), np.linspace(-3.0, 3.0, 25))
    background_xyz = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    background = np.column_stack(
        [background_xyz, np.full(len(background_xyz), 20.0)]
    ).astype(np.float32)

    rng = np.random.default_rng(7)
    object_xyz = rng.normal(
        loc=(5.0, 0.0, 1.0), scale=(0.12, 0.12, 0.35), size=(60, 3)
    )
    moving = np.column_stack([object_xyz, np.full(len(object_xyz), 150.0)]).astype(
        np.float32
    )

    for _ in range(warmup_frames):
        yield background
    for _ in range(moving_frames):
        yield np.vstack([background, moving])
    for _ in range(clear_frames):
        yield background


class SyntheticCloudPublisher(Node):
    def __init__(
        self,
        topic: str,
        rate_hz: float,
        warmup: int,
        moving_frames: int,
        clear_frames: int,
        start_delay: float,
    ):
        super().__init__("na70_synthetic_cloud_publisher")
        self.publisher = self.create_publisher(PointCloud2, topic, CLOUD_QOS)
        self.frames = iter(make_frames(warmup, moving_frames, clear_frames))
        self.start_time = time.monotonic()
        self.start_delay = start_delay
        self.timer = self.create_timer(1.0 / rate_hz, self._publish_frame)
        self.get_logger().info(
            f"Publishing synthetic PointCloud2 on {topic} after {start_delay:.1f}s"
        )

    def _publish_frame(self):
        if time.monotonic() - self.start_time < self.start_delay:
            return
        try:
            points = next(self.frames)
        except StopIteration:
            self.get_logger().info("Synthetic sequence complete")
            self.timer.cancel()
            rclpy.shutdown()
            return

        header = self.get_clock().now().to_msg()
        from std_msgs.msg import Header

        msg_header = Header(stamp=header, frame_id="lidar_link")
        fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
            PointField(name="intensity", offset=12, datatype=PointField.FLOAT32, count=1),
        ]
        self.publisher.publish(point_cloud2.create_cloud(msg_header, fields, points.tolist()))


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="/inno_lidar_cloud")
    parser.add_argument("--rate", type=float, default=10.0)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--moving-frames", type=int, default=3)
    parser.add_argument("--clear-frames", type=int, default=4)
    parser.add_argument("--start-delay", type=float, default=3.0)
    parsed, remaining = parser.parse_known_args(args)

    rclpy.init(args=remaining)
    node = SyntheticCloudPublisher(
        parsed.topic,
        parsed.rate,
        parsed.warmup,
        parsed.moving_frames,
        parsed.clear_frames,
        parsed.start_delay,
    )
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
