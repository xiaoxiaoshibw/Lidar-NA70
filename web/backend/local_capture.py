"""WebUI 本机最小双流接收：IMU USB 串口 + NA70 网口 UDP，只录原始数据。

只负责"同时收到、如实落盘"，不处理、不判定：复用 ``na70_core.capture``
的 ``CaptureService`` 状态机与 ``CaptureRunner`` 质量落盘，雷达端用
``UdpLidarCapture``（Windows 无 tcpdump，普通 UDP socket + 合成帧头，
见 ``lidar_udp.py``）。会话目录 ``radar_capture_data/na70_sessions/``，
布局与 Jetson 会话一致，后续标定/批处理工具可直接读取。

前提：雷达 UDP 目的 IP 必须配置为本机 IP（雷达单播发包）。
"""
from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from na70_core.capture.lidar_udp import UdpLidarCapture
from na70_core.capture.runner import CaptureRunner
from na70_core.capture.service import CaptureService

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION_ROOT = REPO_ROOT / "radar_capture_data" / "na70_sessions"
MANUAL_DURATION_S = 1800.0  # duration_s 缺省时按手动停止处理，服务端兜底上限
MAX_DURATION_S = 1800.0
DEFAULT_IMU_BAUD = 460800
DEFAULT_LIDAR_PORT = 2368
ALLOWED_FIELDS = {"task", "duration_s", "imu_port", "imu_baud", "lidar_port"}


def local_ips():
    """本机 IPv4 候选（按地址排序），供雷达 UDP 目的 IP 配置提示。"""
    addresses = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 9))  # UDP connect 不发包，只为取路由源地址
            addresses.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


class LocalCaptureRuns:
    """把 CaptureService 包装成 /api/local-capture 的最小 start/stop/status。"""

    def __init__(self, root=None, *, service=None):
        self.root = Path(root or os.environ.get("NA70_LOCAL_CAPTURE_ROOT")
                         or DEFAULT_SESSION_ROOT)
        self.service = service or CaptureService(
            self.root, runner_factory=self._make_runner)
        self.router = APIRouter(prefix="/api/local-capture")
        self._pending = {}
        self._start_lock = threading.Lock()
        self._routes()

    def _make_runner(self, session):
        pending = dict(self._pending)
        lidar = UdpLidarCapture(session, port=int(pending.get("lidar_port", DEFAULT_LIDAR_PORT)))
        imu = {"port": pending["imu_port"],
               "baud": int(pending.get("imu_baud", DEFAULT_IMU_BAUD))}
        return CaptureRunner(session, imu=imu, lidar=lidar, poll_interval=0.5)

    def _routes(self):
        router = self.router

        @router.get("/status")
        def status():
            payload = self.service.status()
            payload["local_ips"] = local_ips()
            return payload

        @router.post("/start", status_code=202)
        def start(payload: dict):
            unknown = set(payload) - ALLOWED_FIELDS
            if unknown:
                raise HTTPException(422, f"未知字段: {sorted(unknown)}")
            task = payload.get("task") or "MANUAL"
            if not isinstance(task, str) or not task.strip():
                raise HTTPException(422, "task 必须为非空字符串")
            imu_port = payload.get("imu_port")
            if not isinstance(imu_port, str) or not imu_port.strip():
                raise HTTPException(422, "imu_port 必须为串口设备名（如 COM3）")
            imu_baud = payload.get("imu_baud", DEFAULT_IMU_BAUD)
            if not isinstance(imu_baud, int) or isinstance(imu_baud, bool) or imu_baud <= 0:
                raise HTTPException(422, "imu_baud 必须为正整数")
            lidar_port = payload.get("lidar_port", DEFAULT_LIDAR_PORT)
            if not isinstance(lidar_port, int) or isinstance(lidar_port, bool) \
                    or lidar_port != DEFAULT_LIDAR_PORT:
                raise HTTPException(422, "lidar_port 必须为 2368，现有解码器仅支持此端口")
            duration = payload.get("duration_s")
            if duration is None:
                duration = MANUAL_DURATION_S
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) \
                    or not 0 < duration <= MAX_DURATION_S:
                raise HTTPException(
                    422, f"duration_s 须为 0–{MAX_DURATION_S:g} 秒；留空表示手动停止")
            with self._start_lock:
                if self.service.status()["state"] in ("starting", "recording", "stopping"):
                    raise HTTPException(409, "已有会话在进行，请等它结束")
                self._pending = {"imu_port": imu_port, "imu_baud": imu_baud,
                                 "lidar_port": lidar_port}
                try:
                    return self.service.start(task=task.strip(), duration_s=float(duration),
                                              operator={}, note="")
                except RuntimeError as exc:
                    raise HTTPException(409, str(exc)) from exc

        @router.post("/stop")
        def stop():
            return self.service.request_stop()

    def shutdown(self):
        """应用退出时请求停止进行中的录制，避免句柄悬挂。"""
        try:
            self.service.request_stop()
            self.service.wait(timeout=10)
        except Exception:  # noqa: BLE001 - 退出路径不再抛错
            pass
