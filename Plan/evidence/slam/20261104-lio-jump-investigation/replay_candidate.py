"""Replay a NA70 LIO package as ROS2 PointCloud2 + Imu for FAST-LIO.

Run inside WSL2/Humble after sourcing ROS2:
    python3 tools/lio/replay_na70_package.py --package <dir> --pace 0.1 --linger 3

The scan header stamp is the frame start; each point's `time` field is seconds
relative to that start.  IMU samples are published up to the scan end before the
scan so FAST-LIO can synchronise.  This is a plumbing prototype, not a
calibration or accuracy claim.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np


def _load(package):
    package = Path(package)
    scans_path = package / "scans.npz"
    imu_path = package / "imu.npz"
    if not scans_path.is_file() or not imu_path.is_file():
        raise SystemExit(f"package needs scans.npz and imu.npz: {package}")
    with np.load(scans_path, allow_pickle=False) as archive:
        scans = {key: np.array(archive[key], copy=True) for key in archive.files}
    with np.load(imu_path, allow_pickle=False) as archive:
        imu = {key: np.array(archive[key], copy=True) for key in archive.files}
    manifest = {}
    manifest_path = package / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return scans, imu, manifest


def _stamp(seconds):
    from builtin_interfaces.msg import Time
    whole = int(math.floor(seconds))
    return Time(sec=whole, nanosec=int(round((seconds - whole) * 1e9)))


def _pointcloud2(points, times, frame_id, stamp):
    from sensor_msgs.msg import PointCloud2, PointField
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                      ("intensity", "<f4"), ("time", "<f4")])
    record = np.zeros(len(points), dtype=dtype)
    record["x"], record["y"], record["z"] = points[:, 0], points[:, 1], points[:, 2]
    record["intensity"] = points[:, 3] if points.shape[1] > 3 else 0.0
    record["time"] = times
    message = PointCloud2()
    message.header.stamp = stamp
    message.header.frame_id = frame_id
    message.height = 1
    message.width = len(record)
    message.is_bigendian = False
    message.point_step = dtype.itemsize
    message.row_step = dtype.itemsize * len(record)
    message.is_dense = True
    message.fields = [
        PointField(name=name, offset=offset, datatype=PointField.FLOAT32, count=1)
        for name, offset in (("x", 0), ("y", 4), ("z", 8), ("intensity", 12), ("time", 16))]
    message.data = record.tobytes()
    return message


def replay(package, *, pace=0.1, linger=3.0, frame_limit=None, discovery_wait=1.0):
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import Imu, PointCloud2

    scans, imu, manifest = _load(package)
    origin_value = scans.get("origin_unix_s", 0.0)
    origin = float(np.asarray(origin_value).reshape(-1)[0]) if np.size(origin_value) else 0.0
    if origin == 0.0 and manifest.get("scans", {}).get("origin_unix_s"):
        origin = float(manifest["scans"]["origin_unix_s"])
    frame_offsets = scans["frame_offsets"]
    frame_start = scans["frame_start_s"]
    frame_end = scans["frame_end_s"]
    point_time = scans["point_time_s"]
    points = scans["points"]
    n_frames = len(frame_start)
    if frame_limit:
        n_frames = min(n_frames, frame_limit)

    rclpy.init()
    node = Node("na70_replay")
    points_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
    points_pub = node.create_publisher(PointCloud2, "/na70/points", points_qos)
    imu_pub = node.create_publisher(Imu, "/na70/imu", 20)
    matched_after = None
    deadline = time.monotonic() + max(0.0, discovery_wait)
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
        if points_pub.get_subscription_count() and imu_pub.get_subscription_count():
            matched_after = discovery_wait - max(0.0, deadline - time.monotonic())
            break
        time.sleep(0.05)
    imu_t = imu["t_host_unix"]
    imu_g = np.radians(imu["gyro_deg_s"])
    imu_a = imu["accel_m_s2"]
    imu_index = 0
    published_imu = 0
    published_scans = 0

    def publish_imu(index):
        message = Imu()
        message.header.stamp = _stamp(float(imu_t[index]))
        message.header.frame_id = "imu_link"
        message.angular_velocity.x = float(imu_g[index, 0])
        message.angular_velocity.y = float(imu_g[index, 1])
        message.angular_velocity.z = float(imu_g[index, 2])
        message.linear_acceleration.x = float(imu_a[index, 0])
        message.linear_acceleration.y = float(imu_a[index, 1])
        message.linear_acceleration.z = float(imu_a[index, 2])
        message.orientation.w = 1.0
        message.orientation_covariance[0] = -1.0
        imu_pub.publish(message)
        # DIAGNOSTIC ONLY: test whether fixed pacing helps this probe receive bursts.
        # This does not reconstruct IMU sample time or ensure FAST-LIO delivery.
        time.sleep(0.001)

    for frame in range(n_frames):
        end = origin + float(frame_end[frame])
        while imu_index < len(imu_t) and float(imu_t[imu_index]) <= end:
            publish_imu(imu_index)
            imu_index += 1
            published_imu += 1
        start_index = int(frame_offsets[frame])
        stop_index = int(frame_offsets[frame + 1])
        relative = point_time[start_index:stop_index] - float(frame_start[frame])
        message = _pointcloud2(points[start_index:stop_index], relative.astype(np.float32),
                               "na70_lidar", _stamp(origin + float(frame_start[frame])))
        points_pub.publish(message)
        published_scans += 1
        rclpy.spin_once(node, timeout_sec=0.0)
        time.sleep(pace)

    # Feed a short IMU tail so FAST-LIO can close the last scan.
    tail_end = origin + float(frame_end[n_frames - 1]) + max(0.5, linger)
    while imu_index < len(imu_t) and float(imu_t[imu_index]) <= tail_end:
        publish_imu(imu_index)
        imu_index += 1
        published_imu += 1
    deadline = time.monotonic() + linger
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
    subscribers = {"points": points_pub.get_subscription_count(),
                   "imu": imu_pub.get_subscription_count()}
    node.destroy_node()
    rclpy.shutdown()
    return {"scans": published_scans, "imu": published_imu, "frames_available": len(frame_start),
            "subscribers": subscribers, "matched_after_s": matched_after}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--pace", type=float, default=0.1, help="seconds between scans")
    parser.add_argument("--linger", type=float, default=3.0)
    parser.add_argument("--discovery-wait", type=float, default=1.0)
    parser.add_argument("--frames", type=int)
    args = parser.parse_args(argv)
    result = replay(args.package, pace=args.pace, linger=args.linger,
                    frame_limit=args.frames, discovery_wait=args.discovery_wait)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
