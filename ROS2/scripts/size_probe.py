#!/usr/bin/env python3
"""Publish std_msgs/String of several sizes to find the DDS transport limit."""
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class SizeProbe(Node):
    SIZES = [16, 256, 1024, 4096, 8192, 12000, 16384, 32768, 65536, 131072]

    def __init__(self):
        super().__init__("size_probe")
        self.pubs = {
            s: self.create_publisher(String, f"/probe_{s}", 10) for s in self.SIZES
        }
        self.timer = self.create_timer(0.2, self._cb)
        self.get_logger().info(f"publishing on {len(self.SIZES)} probe topics")

    def _cb(self):
        for s, pub in self.pubs.items():
            msg = String()
            msg.data = "x" * s
            pub.publish(msg)


def main():
    rclpy.init()
    node = SizeProbe()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()