"""本机双流接收 WebUI 契约测试：fake runner，不碰真实串口/网卡。"""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from na70_core.capture.service import CaptureService
from test_na70_capture_service import FakeRunner


def wait_idle(client, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get("/api/local-capture/status").json()
        if payload["state"] in ("idle", "failed"):
            return payload
        time.sleep(0.05)
    raise AssertionError("会话未在超时内结束")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NA70_LOCAL_CAPTURE_ROOT", str(tmp_path / "sessions"))
    app = create_app(tmp_path / "store")
    runs = app.state.local_capture_runs
    # 注入 fake runner：不碰串口/网卡，只产生会话文件与可预期的状态。
    runs.service = CaptureService(
        runs.root, runner_factory=lambda session: FakeRunner(session),
        min_free_bytes=0, tick_interval=0.05)
    with TestClient(app) as test_client:
        yield test_client


def test_start_stop_and_status_flow(client, tmp_path):
    response = client.post("/api/local-capture/start", json={
        "task": "TEST-3S", "duration_s": 3, "imu_port": "COM3"})
    assert response.status_code == 202
    assert response.json()["state"] == "starting"
    final = wait_idle(client)
    assert final["last"]["session_id"]
    assert final["last"]["status"] == "ok"
    session_dir = tmp_path / "sessions" / final["last"]["session_id"]
    assert (session_dir / "manifest.json").exists()
    assert (session_dir / "quality.json").exists()
    status = client.get("/api/local-capture/status").json()
    assert status["local_ips"]  # 雷达目的 IP 提示可用
    sessions = status["sessions"]
    assert sessions and sessions[0]["task"] == "TEST-3S"


def test_manual_stop_finishes_early(client):
    response = client.post("/api/local-capture/start", json={
        "imu_port": "COM3", "duration_s": 60})
    assert response.status_code == 202
    time.sleep(0.3)
    # 时长缺省按手动停止处理；这里显式 60 s 但提前停止
    assert client.post("/api/local-capture/stop").status_code == 200
    final = wait_idle(client)
    assert final["last"]["duration_s"] < 10


def test_second_start_conflict_returns_409(client):
    assert client.post("/api/local-capture/start", json={
        "imu_port": "COM3", "duration_s": 30}).status_code == 202
    conflict = client.post("/api/local-capture/start", json={
        "imu_port": "COM3", "duration_s": 30})
    assert conflict.status_code == 409
    assert "会话" in conflict.json()["detail"]
    client.post("/api/local-capture/stop")
    wait_idle(client)


def test_conflicting_start_does_not_change_pending_devices(client):
    runs = client.app.state.local_capture_runs
    entered, release = threading.Event(), threading.Event()
    seen = []

    def factory(session):
        entered.set()
        release.wait(5)
        seen.append(dict(runs._pending))
        return FakeRunner(session)

    runs.service = CaptureService(runs.root, runner_factory=factory,
                                  min_free_bytes=0, tick_interval=0.05)
    try:
        assert client.post("/api/local-capture/start", json={"imu_port": "COM3"}).status_code == 202
        assert entered.wait(5)
        assert client.post("/api/local-capture/start", json={"imu_port": "COM4"}).status_code == 409
    finally:
        release.set()
    client.post("/api/local-capture/stop")
    wait_idle(client)
    assert seen[0]["imu_port"] == "COM3"


def test_validation_errors(client):
    cases = [
        ({"imu_port": "COM3", "duration_s": 10, "oops": 1}, "未知字段"),       # 未知字段
        ({"duration_s": 10}, "imu_port"),                                     # 缺串口
        ({"imu_port": "COM3", "duration_s": 0}, "duration_s"),                # 非正时长
        ({"imu_port": "COM3", "duration_s": 9999}, "duration_s"),             # 超上限
        ({"imu_port": "COM3", "lidar_port": 0}, "lidar_port"),                # 非法端口
        ({"imu_port": "COM3", "lidar_port": 9999}, "lidar_port"),             # 解码器不支持
        ({"imu_port": "COM3", "imu_baud": "fast"}, "imu_baud"),               # 非法波特率
    ]
    for payload, expected in cases:
        response = client.post("/api/local-capture/start", json=payload)
        assert response.status_code == 422, payload
        assert expected in response.json()["detail"]


def test_default_task_is_manual(client, tmp_path):
    assert client.post("/api/local-capture/start", json={
        "imu_port": "COM3", "duration_s": 3}).status_code == 202
    final = wait_idle(client)
    assert final["last"]["session_id"]
    operator = json.loads((tmp_path / "sessions" / final["last"]["session_id"]
                           / "operator.json").read_text("utf-8"))
    assert operator["operator"]["task"] == "MANUAL"


def test_failed_runner_marks_state_failed(client):
    runs = client.app.state.local_capture_runs
    runs.service = CaptureService(
        runs.root, runner_factory=lambda session: (_ for _ in ()).throw(RuntimeError("设备掉线")),
        min_free_bytes=0)
    response = client.post("/api/local-capture/start", json={
        "imu_port": "COM3", "duration_s": 3})
    assert response.status_code == 202
    final = wait_idle(client)
    assert final["state"] == "failed"
    assert "设备掉线" in final["error"]
    # runner 构造失败发生在 service 记住 session 之前，用落盘的 manifest 验证目录保留
    manifests = list(runs.root.glob("*/manifest.json"))
    assert manifests, "失败会话目录必须保留"
    assert json.loads(manifests[0].read_text("utf-8"))["status"] == "failed"
