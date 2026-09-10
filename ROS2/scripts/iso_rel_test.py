#!/usr/bin/env python3
"""Cross-process RELIABLE 12KB test (control group)."""
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
import time
import sys

PAYLOAD = 12000
TOPIC = "/iso_rel_12k"
RELIABLE_QOS = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)


class PubNode(Node):
    def __init__(self):
        super().__init__("iso_pub_rel")
        self.pub = self.create_publisher(String, TOPIC, RELIABLE_QOS)
        self.timer = self.create_timer(1.0, self._send)
        self.count = 0
        self.get_logger().info(f"PUB RELIABLE {PAYLOAD}B @ {TOPIC} 1Hz")

    def _send(self):
        self.count += 1
        m = String()
        m.data = "x" * PAYLOAD
        self.pub.publish(m)
        self.get_logger().info(f"pub #{self.count} {PAYLOAD}B")


class SubNode(Node):
    def __init__(self):
        super().__init__("iso_sub_rel")
        self.received = 0
        self.sub = self.create_subscription(String, TOPIC, self._cb, RELIABLE_QOS)
        self.get_logger().info(f"SUB RELIABLE {PAYLOAD}B @ {TOPIC}")

    def _cb(self, msg):
        self.received += 1
        self.get_logger().info(f"RECEIVED #{self.received} len={len(msg.data)}")


def main():
    rclpy.init()
    if "--pub-only" in sys.argv:
        node = PubNode()
    elif "--sub-only" in sys.argv:
        node = SubNode()
    else:
        print("usage: --pub-only or --sub-only")
        sys.exit(1)
    start = time.time()
    while time.time() - start < 10:
        rclpy.spin_once(node, timeout_sec=0.5)
    if hasattr(node, "received"):
        node.get_logger().info(f"done: received {node.received}")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
