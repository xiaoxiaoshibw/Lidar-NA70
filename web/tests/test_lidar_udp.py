"""本机 UDP 雷达抓包测试：合成的 PCAP 必须能被 decoder.scan_pcap 读回。"""

import socket
import threading
import time

import pytest

from backend.decoder import scan_pcap
from na70_core.capture.lidar_udp import LidarError, UdpLidarCapture
from na70_core.capture.runner import CaptureRunner
from na70_core.capture.session import CaptureSession
from test_decoder import make_pcap  # noqa: F401 - 保持与既有用例同源
from test_thin_wire import timed_payload


@pytest.fixture()
def udp_pair():
    """绑定回环 2368（decoder 只认目的端口 2368；被占用则跳过）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 2368))
    except OSError as exc:
        sock.close()
        pytest.skip(f"UDP 2368 被占用，无法验证 decoder 兼容性：{exc}")
    sock.settimeout(0.2)
    port = sock.getsockname()[1]
    yield sock, port
    sock.close()


def make_capture(session, sock):
    return UdpLidarCapture(session, port=sock.getsockname()[1],
                           socket_factory=lambda: sock)


def stream_udp(port, count, interval=0.002):
    """后台假雷达：向回环端口发送 NA70 1410 B 载荷。"""

    def job():
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for index in range(count):
                sender.sendto(timed_payload(1.0 + index * 0.001), ("127.0.0.1", port))
                time.sleep(interval)
        finally:
            sender.close()

    thread = threading.Thread(target=job, daemon=True)
    thread.start()
    return thread


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_udp_capture_writes_decoder_compatible_pcap(tmp_path, udp_pair):
    sock, _port = udp_pair
    session = CaptureSession(tmp_path, task="SYNTHETIC")
    capture = make_capture(session, sock)
    capture.start()
    stream_udp(sock.getsockname()[1], count=120)
    assert wait_until(lambda: capture.status()["captured"] >= 120)
    capture.stop()
    status = capture.status()
    assert status["captured"] == 120
    assert status["received"] == 120
    assert status["dropped"] is None
    assert status["pcap_bytes"] > 24
    assert status["local_ip"] == "127.0.0.1"
    assert status["destination_source"] == "bound_address"
    scan = scan_pcap(capture.pcap)
    assert scan.valid_packets == 120
    assert scan.invalid_packets == 0
    assert sum(frame["packet_count"] for frame in scan.frames) == 120


def test_wildcard_destination_is_unknown_not_route_guess(tmp_path):
    capture = UdpLidarCapture(CaptureSession(tmp_path, task="SYNTHETIC"), port=2368)
    try:
        capture.start()
    except LidarError as exc:
        pytest.skip(f"UDP 2368 不可用：{exc}")
    try:
        stream_udp(2368, count=1)
        assert wait_until(lambda: capture.status()["captured"] == 1)
    finally:
        capture.stop()
    status = capture.status()
    assert status["local_ip"] is None
    assert status["destination_source"] == "unknown"
    assert status["synthetic_destination_ip"] == "0.0.0.0"
    # Global + record + Ethernet + IPv4 destination offset.
    assert capture.pcap.read_bytes()[70:74] == socket.inet_aton("0.0.0.0")
    assert scan_pcap(capture.pcap).valid_packets == 1


def test_runner_with_udp_capture_reports_unknown_drops(tmp_path, udp_pair):
    import json

    sock, port = udp_pair
    session = CaptureSession(tmp_path, task="SYNTHETIC")
    capture = make_capture(session, sock)
    runner = CaptureRunner(session, imu=None, lidar=capture, poll_interval=0.05)
    runner.start()
    stream_udp(port, count=200, interval=0.005)
    manifest = runner.run(2.0)
    assert manifest["status"] == "degraded"
    data = json.loads((session.path / "quality.json").read_text("utf-8"))
    by_name = {check["name"]: check["ok"] for check in data["checks"]}
    assert by_name["lidar_bytes"] is True
    assert by_name["lidar_drops"] is False
    assert data["status"] == "degraded"


def test_live_rate_decays_after_stream_stops(tmp_path):
    now = [10.0]
    capture = UdpLidarCapture(CaptureSession(tmp_path, task="SYNTHETIC"), clock=lambda: now[0])
    capture.state = "live"
    capture.started_mono = 9.0
    capture._arrivals.append(10.0)
    assert capture.status()["packets_per_sec"] > 0
    now[0] = 13.0
    assert capture.status()["packets_per_sec"] == 0


def test_exclusive_port_bind_fails_when_in_use(tmp_path):
    first = UdpLidarCapture(CaptureSession(tmp_path, task="SYNTHETIC"), port=0, bind_host="127.0.0.1")
    first.start()
    try:
        port = first._sock.getsockname()[1]
        second = UdpLidarCapture(CaptureSession(tmp_path, session_id="second", task="SYNTHETIC"), port=port, bind_host="127.0.0.1")
        with pytest.raises(LidarError, match="端口"):
            second.start()
    finally:
        first.stop()


def test_second_start_rejected_and_pcap_guard(tmp_path, udp_pair):
    sock, port = udp_pair
    session = CaptureSession(tmp_path, task="SYNTHETIC")
    capture = make_capture(session, sock)
    capture.start()
    with pytest.raises(LidarError):
        capture.start()
    capture.stop()
    assert capture.status()["state"] == "stopped"
    # 非空 PCAP 拒绝覆盖：新会话目录写入内容后再次 start 必须报错
    session.file("raw/lidar.pcap").write_bytes(b"stale")
    fresh = UdpLidarCapture(session, port=port)
    with pytest.raises(LidarError):
        fresh.start()


def test_bind_failure_raises_lidar_error(tmp_path):
    session = CaptureSession(tmp_path, task="SYNTHETIC")

    def broken_factory():
        raise OSError("模拟端口被占用")

    capture = UdpLidarCapture(session, port=2368, socket_factory=broken_factory)
    with pytest.raises(LidarError, match="端口"):
        capture.start()
    assert capture.status()["state"] == "idle"
