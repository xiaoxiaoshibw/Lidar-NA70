"""WebUI 本机双流采集：IMU USB 串口 + NA70 网口 UDP，同机同进程。

与 Jetson 链路（``na70_core.capture.service`` 的 8791 服务 + iframe 页）并行：

* 复用 ``CaptureService`` 的单会话状态机（idle/starting/recording/stopping/failed、
  失败会话保留、磁盘门禁、会话列举），只是把 tcpdump 换成 Windows 可用的
  ``UdpLidarCapture``（普通 UDP socket + 合成帧头，见 ``lidar_udp.py``）。
* 会话目录与本机镜像同根：``radar_capture_data/na70_sessions/``，布局与
  Jetson 会话一致，``tools/report_na70_calibration_batch.py`` 可直接读取。
* 一次只跑一个会话（串口独占）；服务重启时录制中的会话在 ``sessions()``
  里显示为 ``unfinalized``，不自动重跑。
* 前提：雷达 UDP 目的 IP 必须配置为本机 IP（雷达单播发包，见 quality.json
  的 measurement_limits，两路仍只提供关联不构成硬件同步）。
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from na70_core.capture.lidar_udp import UdpLidarCapture
from na70_core.capture.runner import CaptureRunner
from na70_core.capture.service import CaptureService

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION_ROOT = REPO_ROOT / "radar_capture_data" / "na70_sessions"
MANUAL_DURATION_S = 1800.0  # duration_s 缺省时按手动停止处理，服务端兜底上限
MIN_DURATION_S = 3.0
MAX_DURATION_S = 1800.0
DEFAULT_IMU_BAUD = 460800
ALLOWED_FIELDS = {"task", "duration_s", "operator", "note", "imu_port", "imu_baud", "lidar_port"}
BODY_LIMIT_BYTES = 65_536


def local_ips():
    """本机 IPv4 候选（默认路由优先），供雷达 UDP 目的 IP 配置提示。"""
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


def serial_ports():
    """可用串口列表；pyserial 缺失时返回空列表而不是让页面不可用。"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"device": item.device, "description": item.description}
            for item in sorted(list_ports(), key=lambda item: item.device)]


class LocalCaptureRuns:
    """把 CaptureService 包装成 /api/local-capture 路由（单 worker 语义）。"""

    def __init__(self, root=None, *, service=None):
        self.root = Path(root or os.environ.get("NA70_LOCAL_CAPTURE_ROOT")
                         or DEFAULT_SESSION_ROOT)
        self.service = service or CaptureService(
            self.root, runner_factory=self._make_runner)
        self.router = APIRouter(prefix="/api/local-capture")
        self._pending = {}
        self._routes()

    # ------------------------------------------------------------------ runner
    def _make_runner(self, session):
        pending = dict(self._pending)
        imu_port = pending.get("imu_port")
        imu = None
        if imu_port:
            imu = {"port": imu_port, "baud": int(pending.get("imu_baud", DEFAULT_IMU_BAUD))}
        lidar = UdpLidarCapture(session, port=int(pending.get("lidar_port", 2368)))
        return CaptureRunner(session, imu=imu, lidar=lidar, poll_interval=0.5)

    # ------------------------------------------------------------------- routes
    def _routes(self):
        router = self.router

        @router.get("/status")
        def status():
            return self.service.status()

        @router.post("/start", status_code=202)
        def start(payload: dict):
            unknown = set(payload) - ALLOWED_FIELDS
            if unknown:
                raise HTTPException(422, f"未知字段: {sorted(unknown)}")
            task = payload.get("task")
            if not isinstance(task, str) or not task.strip():
                raise HTTPException(422, "task 必须为非空字符串")
            imu_port = payload.get("imu_port")
            if not isinstance(imu_port, str) or not imu_port.strip():
                raise HTTPException(422, "imu_port 必须为串口设备名（如 COM3）")
            imu_baud = payload.get("imu_baud", DEFAULT_IMU_BAUD)
            if not isinstance(imu_baud, int) or isinstance(imu_baud, bool) or imu_baud <= 0:
                raise HTTPException(422, "imu_baud 必须为正整数")
            lidar_port = payload.get("lidar_port", 2368)
            if not isinstance(lidar_port, int) or isinstance(lidar_port, bool) \
                    or not 1 <= lidar_port <= 65535:
                raise HTTPException(422, "lidar_port 必须为 1–65535 的整数")
            duration = payload.get("duration_s")
            if duration is None:
                duration = MANUAL_DURATION_S
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) \
                    or not MIN_DURATION_S <= duration <= MAX_DURATION_S:
                raise HTTPException(
                    422, f"duration_s 须在 {MIN_DURATION_S:g}–{MAX_DURATION_S:g} 秒；"
                         "留空表示手动停止（按上限计时）")
            operator = payload.get("operator")
            if operator is not None and not isinstance(operator, dict):
                raise HTTPException(422, "operator 必须为对象")
            note = payload.get("note")
            if note is not None and not isinstance(note, str):
                raise HTTPException(422, "note 必须为字符串")
            self._pending = {"imu_port": imu_port, "imu_baud": imu_baud,
                             "lidar_port": lidar_port}
            try:
                return self.service.start(
                    task=task.strip(), duration_s=float(duration),
                    operator=operator or {}, note=note or "")
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc

        @router.post("/stop")
        def stop():
            return self.service.request_stop()

        @router.get("/sessions")
        def sessions(limit: int = Query(20, ge=1, le=200)):
            return self.service.sessions(limit=limit)

        @router.get("/ports")
        def ports():
            return {"serial_ports": serial_ports(), "local_ips": local_ips()}

        @router.get("/files")
        def files(session: str):
            try:
                return self.service.list_files(session)
            except ValueError as exc:
                raise HTTPException(404 if "不存在" in str(exc) else 400, str(exc)) from exc

        @router.get("/file")
        def file(session: str, path: str):
            try:
                target = self.service.read_file(session, path)
            except ValueError as exc:
                raise HTTPException(404 if "不存在" in str(exc) else 400, str(exc)) from exc
            return FileResponse(target, filename=Path(path).name)

    # ----------------------------------------------------------------- lifecycle
    def shutdown(self):
        """应用退出时请求停止进行中的录制，避免句柄悬挂。"""
        try:
            self.service.request_stop()
            self.service.wait(timeout=10)
        except Exception:  # noqa: BLE001 - 退出路径不再抛错
            pass
