"""Measure delivery to this lightweight probe, not to FAST-LIO."""
import collections
import json
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu, PointCloud2


rclpy.init()
node = Node("na70_probe")
counts = collections.Counter()
clouds = [0]

def imu_cb(msg):
    counts[msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec] += 1

def cloud_cb(_msg):
    clouds[0] += 1

imu_sub = node.create_subscription(Imu, "/na70/imu", imu_cb, 10)
cloud_sub = node.create_subscription(PointCloud2, "/na70/points", cloud_cb, qos_profile_sensor_data)
deadline = time.monotonic() + 36
while time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.05)
print(json.dumps({"interpretation": "lightweight probe only; not FAST-LIO receive count",
                  "imu_received": sum(counts.values()), "distinct_imu_stamps": len(counts),
                  "clouds_received": clouds[0], "imu_counts_by_stamp_ns": dict(sorted(counts.items()))}))
node.destroy_node()
rclpy.shutdown()
