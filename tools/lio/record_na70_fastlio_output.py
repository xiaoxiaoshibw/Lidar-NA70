"""Record FAST-LIO /Odometry and the latest /cloud_registered to files.

Run alongside the launch/replay in the same shell (CycloneDDS) so discovery is
shared.  Outputs are diagnostic, not accuracy evidence.
"""
from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

import numpy as np


def _write_ply(path, points, intensity):
    record = np.zeros(len(points), dtype=np.dtype([
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("intensity", "u1")]))
    record["x"], record["y"], record["z"] = points[:, 0], points[:, 1], points[:, 2]
    record["intensity"] = np.clip(intensity, 0, 255).astype(np.uint8)
    with Path(path).open("wb") as handle:
        handle.write(b"ply\nformat binary_little_endian 1.0\n"
                     + f"element vertex {len(record)}\n".encode()
                     + b"property float x\nproperty float y\nproperty float z\n"
                     + b"property uchar intensity\nend_header\n")
        handle.write(record.tobytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odom", type=Path, required=True)
    parser.add_argument("--cloud", type=Path)
    parser.add_argument("--duration", type=float, default=25.0)
    args = parser.parse_args(argv)

    import rclpy
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import PointCloud2
    from sensor_msgs_py import point_cloud2

    rclpy.init()
    node = rclpy.create_node("na70_fastlio_recorder")
    poses = []
    latest = {"msg": None}

    def on_odom(message):
        p = message.pose.pose.position
        q = message.pose.pose.orientation
        poses.append((message.header.stamp.sec + message.header.stamp.nanosec * 1e-9,
                      p.x, p.y, p.z, q.x, q.y, q.z, q.w))

    def on_cloud(message):
        latest["msg"] = message

    node.create_subscription(Odometry, "/Odometry", on_odom, 20)
    node.create_subscription(PointCloud2, "/cloud_registered", on_cloud, 10)
    deadline = time.monotonic() + args.duration
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
    with args.odom.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t_s", "x", "y", "z", "qx", "qy", "qz", "qw"])
        writer.writerows(poses)
    cloud_points = 0
    if args.cloud is not None and latest["msg"] is not None:
        rows = list(point_cloud2.read_points(latest["msg"], field_names=("x", "y", "z", "intensity"),
                                             skip_nans=True))
        if rows:
            array = np.asarray(rows)
            if array.dtype.names:
                array = np.column_stack([np.asarray(array[name], dtype=np.float64)
                                         for name in array.dtype.names])
            else:
                array = array.astype(np.float64)
            _write_ply(args.cloud, array[:, :3], array[:, 3])
            cloud_points = len(array)
    node.destroy_node()
    rclpy.shutdown()
    print(f"poses={len(poses)} cloud_points={cloud_points}")


if __name__ == "__main__":
    main()