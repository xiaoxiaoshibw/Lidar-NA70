"""Run under WSL2/Humble after building current double_wedge_driver.

Receives actual /inno_lidar_cloud messages, with one acknowledged bundle at a
time so a lost bundle cannot shift the comparison. Uses a separate DDS domain
and UDP port, and only terminates child processes started by this script.
"""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.decoder import scan_pcap, decode_frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[2]
    binary = Path(args.install) / "double_wedge_driver/lib/double_wedge_driver"
    results = []
    rclpy.init()
    node = rclpy.create_node("web_decoder_comparison")
    received = []

    def callback(msg):
        fields = {f.name: f.offset for f in msg.fields}
        cloud = np.ndarray((msg.width * msg.height,), dtype=np.dtype({
            'names': ['x', 'y', 'z', 'intensity'],
            'formats': [('>' if msg.is_bigendian else '<') + 'f4'] * 4,
            'offsets': [fields[k] for k in ['x', 'y', 'z', 'intensity']], 'itemsize': msg.point_step,
        }), buffer=msg.data)
        points = np.column_stack([cloud[k] for k in ['x', 'y', 'z', 'intensity']])
        received.append((points, msg.header.frame_id, msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9))

    sub = node.create_subscription(PointCloud2, "/web_compare/cloud", callback, qos_profile_sensor_data)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for name in ('1.pcap', '2.pcap'):
            path = project / 'radar_capture_data' / name
            scan = scan_pcap(path)
            logs = [(output / (name + suffix)).open('w') for suffix in ('.driver.log', '.merge.log')]
            driver = subprocess.Popen([str(binary / 'double_wedge_driver_lidar'), '--ros-args', '--log-level', 'debug', '-p', 'udp_port:=12368', '-p', 'lidar_topic:=/web_compare/bundle'], stdout=logs[0], stderr=subprocess.STDOUT)
            merge = subprocess.Popen([str(binary / 'double_wedge_driver_test_lidar'), '--ros-args', '--log-level', 'debug', '-p', 'input_topic:=/web_compare/bundle', '-p', 'output_topic:=/web_compare/cloud'], stdout=logs[1], stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while node.count_publishers('/web_compare/cloud') == 0 and time.monotonic() < deadline:
                    rclpy.spin_once(node, timeout_sec=.1)
                if node.count_publishers('/web_compare/cloud') == 0:
                    raise RuntimeError('ROS2 publisher not discovered')
                time.sleep(2)
                maximum = 0.0
                checked = 0
                retries = 0
                reference_clouds = []
                # First 12 consecutive bundles include factory-state inheritance.
                with path.open('rb') as source:
                    for frame in scan.frames[:12]:
                        matched = []
                        for attempt in range(4):
                            received.clear()
                            for offset in frame['offsets']:
                                source.seek(offset)
                                udp.sendto(source.read(1410), ('127.0.0.1', 12368))
                                rclpy.spin_once(node, timeout_sec=0)
                                time.sleep(.003)
                            deadline = time.monotonic() + 3
                            while time.monotonic() < deadline:
                                rclpy.spin_once(node, timeout_sec=.02)
                                matched = [entry for entry in received if frame['device_time'] is not None and abs(entry[2] - frame['device_time']) < 1e-5]
                                if matched: break
                            if matched: break
                            retries += 1
                        if not matched:
                            raise RuntimeError(f'{name} frame {frame["index"]}: no matching ROS2 cloud after retries')
                        actual, frame_id, _ = matched[-1]
                        reference_clouds.append(actual)
                        expected = decode_frame(path, frame)
                        assert actual.shape == expected.shape
                        assert frame_id == frame['frame_id']
                        np.testing.assert_array_equal(actual[:, 3], expected[:, 3])
                        error = float(np.max(np.abs(actual[:, :3] - expected[:, :3])))
                        assert error <= .001, (name, frame['index'], error)
                        maximum = max(maximum, error)
                        checked += len(actual)
                results.append(dict(file=name, frames=12, points=checked, max_coordinate_error_m=maximum, intensity_exact=True, point_order_exact=True, best_effort_retries=retries))
                np.save(output / (name + '.ros2-first12.npy'), np.stack(reference_clouds))
            finally:
                for process in (driver, merge):
                    process.terminate()
                for process in (driver, merge):
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                for log in logs: log.close()
        source = project / 'ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp'
        report = dict(environment='WSL2_HUMBLE_X86_64', driver_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), results=results)
        (output / 'ros2_comparison.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        udp.close(); node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__': main()
