#!/usr/bin/env python3
"""Publish String under RELIABLE and BEST_EFFORT QoS to isolate the failure."""
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String


class QosProbe(Node):
    SIZES = [16, 1024, 12000, 131072]

    def __init__(self):
        super().__init__("qos_probe")
        self.rel = {}
        self.be = {}
        for s in self.SIZES:
            self.rel[s] = self.create_publisher(String, f"/rel_{s}", 10)  # RELIABLE
            self.be[s] = self.create_publisher(
                String, f"/be_{s}", qos_profile_sensor_data  # BEST_EFFORT
            )
        self.timer = self.create_timer(0.2, self._cb)
        self.get_logger().info("qos_probe started")

    def _cb(self):
        for s in self.SIZES:
            m = String()
            m.data = "x" * s
            self.rel[s].publish(m)
            self.be[s].publish(m)


def main():
    rclpy.init()
    node = QosProbe()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()