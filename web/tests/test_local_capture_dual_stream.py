"""SYNTHETIC: one real runner records both streams and finalizes their files."""

import socket
import time

from backend.decoder import scan_pcap
from na70_core.capture.lidar_udp import UdpLidarCapture
from na70_core.capture.runner import CaptureRunner
from na70_core.capture.session import CaptureSession
from test_na70_capture import FakeSerial, build_frame
from test_thin_wire import timed_payload


def test_runner_records_imu_and_udp_together(tmp_path):
    session = CaptureSession(tmp_path, task="SYNTHETIC", min_free_bytes=0)
    imu_bytes = b"".join(build_frame(i) for i in range(10))
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.2)
    receiver_port = receiver.getsockname()[1]
    # The injected test socket uses an ephemeral port to avoid hardware conflicts;
    # the generated PCAP retains the actual NA70 destination port for the decoder.
    lidar = UdpLidarCapture(session, port=2368, socket_factory=lambda: receiver)
    runner = CaptureRunner(
        session, imu={"port": "fake", "serial_factory": lambda *_: FakeSerial([imu_bytes]),
                      "flush_interval": 0.01}, lidar=lidar, poll_interval=0.01)
    runner.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for i in range(100):
                sender.sendto(timed_payload(1.0 + i * 0.001), ("127.0.0.1", receiver_port))
                time.sleep(0.002)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = runner.status()
            if status["imu"]["frames"] == 10 and status["lidar"]["captured"] == 100:
                break
            time.sleep(0.01)
        assert status["imu"]["frames"] == 10
        assert status["lidar"]["captured"] == 100
    finally:
        runner.stop()

    assert (session.path / "raw/imu.bin").read_bytes() == imu_bytes
    scan = scan_pcap(session.path / "raw/lidar.pcap")
    assert scan.valid_packets == 100
    assert scan.invalid_packets == 0
    assert (session.path / "manifest.json").exists()
