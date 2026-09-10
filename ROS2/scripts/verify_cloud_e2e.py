#!/usr/bin/env python3
"""End-to-end verify: synthetic_cloud_publisher -> subscriber, count PointCloud2."""
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2
import time
import sys

TOPIC = "/inno_lidar_cloud"
QOS = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)


class CloudCounter(Node):
    def __init__(self):
        super().__init__("cloud_counter")
        self.count = 0
        self.sub = self.create_subscription(PointCloud2, TOPIC, self._cb, QOS)
        self.get_logger().info(f"subscribing {TOPIC} (RELIABLE)")

    def _cb(self, msg):
        self.count += 1
        self.get_logger().info(
            f"cloud #{self.count}: {msg.width*msg.height} pts, "
            f"row_bytes={msg.row_step}, total={msg.row_step*msg.height}B"
        )


def main():
    rclpy.init()
    node = CloudCounter()
    start = time.time()
    while time.time() - start < 25:
        rclpy.spin_once(node, timeout_sec=0.5)
    node.get_logger().info(f"done: received {node.count} point clouds")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
