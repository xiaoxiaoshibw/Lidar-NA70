"""A2 采集入口服务：Jetson 本机的单页控制台 + HTTP API。

设计约束（对应执行计划 A2）：

* 只绑定 127.0.0.1，由 Windows 侧通过 SSH 端口转发访问；不开放 0.0.0.0。
* 一个进程独占串口，页面只读状态、不直接碰设备。
* 页面只让用户填自己能测的量（墙距/起点/工具精度/摆放方向/备注），
  其余判断全部在服务端完成。
* 停止录制不会删除已写文件；失败会话原样保留，供 AI 事后检查。
"""

from __future__ import annotations

import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .imu import ImuRecorder
from .lidar import LidarCapture
from .runner import CaptureRunner
from .session import CaptureSession, CaptureSessionError

PAGE = Path(__file__).with_name("console.html")
DEFAULT_BY_ID = "/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0003-if00"
TERMINAL_STATES = ("idle", "failed")


def default_imu_port():
    path = Path(DEFAULT_BY_ID)
    return str(path) if path.exists() else "/dev/ttyACM0"


class CaptureService:
    """一次只跑一个会话；所有状态经过锁读写，供 HTTP 轮询。"""

    def __init__(self, root, *, imu_port=None, imu_baud=460800, lidar_iface="enP8p1s0",
                 lidar_port=2368, tcpdump="tcpdump", run_as=None,
                 min_free_bytes=2 * 1024 ** 3, runner_factory=None,
                 clock=time.monotonic, wall=time.time, tick_interval=1.0):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.imu_port = imu_port or default_imu_port()
        self.imu_baud = int(imu_baud)
        self.lidar_iface = lidar_iface
        self.lidar_port = int(lidar_port)
        self.tcpdump = tcpdump
        self.run_as = run_as
        self.min_free_bytes = int(min_free_bytes)
        self._runner_factory = runner_factory
        self.clock = clock
        self.wall = wall
        self.tick_interval = tick_interval
        self._lock = threading.Lock()
        self._thread = None
        self._runner = None
        self._session = None
        self._state = "idle"
        self._task = None
        self._note = ""
        self._operator = {}
        self._duration = 0.0
        self._started_unix = None
        self._ended_unix = None
        self._stop_requested = False
        self._last = None
        self._error = ""
        self._progress = {}
        self._start_mono = None

    # ------------------------------------------------------------------ helpers
    def _make_runner(self, session):
        if self._runner_factory is not None:
            return self._runner_factory(session)
        return CaptureRunner(
            session,
            imu={"port": self.imu_port, "baud": self.imu_baud},
            lidar={"iface": self.lidar_iface, "port": self.lidar_port,
                   "tcpdump": self.tcpdump, "run_as": self.run_as},
            clock=self.clock, wall=self.wall, poll_interval=0.5,
        )

    def sessions(self, limit=20):
        entries = []
        for path in sorted(self.root.iterdir(), reverse=True) if self.root.exists() else []:
            manifest = path / "manifest.json"
            if not path.is_dir():
                continue
            try:
                data = json.loads(manifest.read_text("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("manifest must be an object")
            except (OSError, ValueError):
                if not any((path / name).exists() for name in ("operator.json", "events.jsonl", "raw")):
                    continue
                operator = {}
                try:
                    operator = json.loads((path / "operator.json").read_text("utf-8"))
                    if not isinstance(operator, dict) or not isinstance(operator.get("operator", {}), dict):
                        operator = {}
                except (OSError, ValueError):
                    pass
                is_active = (self._session is not None and self._session.path == path
                             and self._state in ("starting", "recording", "stopping"))
                entries.append({"session_id": path.name,
                                "status": "recording" if is_active else "unfinalized",
                                "task": (operator.get("operator") or {}).get("task"),
                                "created_unix": operator.get("created_unix"),
                                "duration_s": None, "overlap_s": None,
                                "failed_checks": [], "path": str(path)})
                if len(entries) >= limit:
                    break
                continue
            quality = {}
            try:
                quality = json.loads((path / "quality.json").read_text("utf-8"))
                if not isinstance(quality, dict):
                    quality = {}
            except (OSError, ValueError):
                pass
            entries.append({
                "session_id": data.get("session_id", path.name),
                "status": data.get("status"),
                "task": data["operator"].get("task") if isinstance(data.get("operator"), dict) else None,
                "created_unix": data.get("created_unix"),
                "duration_s": quality.get("duration_s"),
                "overlap_s": quality.get("overlap_s"),
                "failed_checks": [c for c in quality.get("checks", []) if not c.get("ok")],
                "path": str(path),
            })
            if len(entries) >= limit:
                break
        return entries

    def status(self):
        with self._lock:
            payload = {
                "state": self._state,
                "error": self._error,
                "task": self._task,
                "note": self._note,
                "operator": dict(self._operator),
                "session_id": self._session.session_id if self._session else None,
                "session_path": str(self._session.path) if self._session else None,
                "duration_s": self._duration,
                "started_unix": self._started_unix,
                "ended_unix": self._ended_unix,
                "remaining_s": round(max(0.0, self._duration - (self.clock() - self._start_mono)), 1)
                if self._state == "recording" and self._duration else 0.0,
                "disk_free_bytes": self._safe_free(),
                "devices": self._runner.status() if self._runner is not None and self._state == "recording" else None,
                "last": self._last,
                "sessions": self.sessions(limit=8),
            }
        return payload

    def _safe_free(self):
        try:
            import shutil
            return shutil.disk_usage(self.root).free
        except OSError:
            return None

    # ------------------------------------------------------------------ control
    def start(self, *, task, duration_s, operator=None, note=""):
        with self._lock:
            if self._state in ("starting", "recording", "stopping"):
                raise RuntimeError(f"已有会话在进行（{self._state}），请等它结束")
            if self._safe_free() is not None and self._safe_free() < self.min_free_bytes:
                raise RuntimeError("磁盘剩余空间不足，拒绝开始")
            self._state = "starting"
            self._task = task
            self._note = note
            self._operator = dict(operator or {})
            self._duration = float(duration_s)
            self._started_unix = self.wall()
            self._ended_unix = None
            self._stop_requested = False
            self._error = ""
            self._last = None
            self._session = None
            self._start_mono = self.clock()
            session_id = self._next_session_id(self._started_unix)
            self._thread = threading.Thread(target=self._job, args=(session_id,),
                                            name="capture-job", daemon=True)
            self._thread.start()
            return {"session_id": session_id, "state": "starting"}

    def _next_session_id(self, unix):
        base = time.strftime("%Y%m%d-%H%M%S", time.localtime(unix))
        candidate, index = base, 2
        while (self.root / candidate).exists():
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def request_stop(self):
        with self._lock:
            if self._state not in ("starting", "recording", "stopping"):
                return {"state": self._state}
            self._stop_requested = True
            self._state = "stopping"
            return {"state": "stopping"}

    def wait(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        return self.status()

    # --------------------------------------------------------------------- job
    def _job(self, session_id):
        session = None
        runner = None
        last = None
        error = ""
        try:
            session = CaptureSession(
                self.root, session_id=session_id, operator={**self._operator, "note": self._note},
                task=self._task, min_free_bytes=self.min_free_bytes, clock=self.wall)
            runner = self._make_runner(session)
            with self._lock:
                self._session, self._runner = session, runner
            runner.start()  # 漏掉这一步会“录了 10 秒但设备从未打开”
            with self._lock:
                self._start_mono = self.clock()
                self._started_unix = self.wall()
                self._state = "stopping" if self._stop_requested else "recording"
            manifest = runner.run(self._duration, tick_interval=self.tick_interval,
                                  should_stop=lambda: self._stop_requested)
            quality = json.loads((session.path / "quality.json").read_text("utf-8"))
            with self._lock:
                self._state = "stopping"
                last = {
                    "session_id": session_id,
                    "status": manifest["status"],
                    "path": str(session.path),
                    "duration_s": quality.get("duration_s"),
                    "failed_checks": [c for c in quality.get("checks", []) if not c["ok"]],
                    "files": [item["path"] for item in manifest["files"]],
                }
        except Exception as exc:  # noqa: BLE001 - 失败必须可见且保留目录
            import traceback
            detail = traceback.format_exc()
            error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._state = "stopping"
            if session is not None:
                try:
                    if runner is not None:
                        runner._safe_stop()
                    session.event("job_failed", error=str(exc), traceback=detail[-4000:])
                    session.finalize(status="failed", quality={"status": "failed", "error": str(exc)},
                                     devices={}, windows={})
                except OSError:
                    pass
        finally:
            with self._lock:
                self._runner = None
                self._last = last
                self._error = error
                self._ended_unix = self.wall()
                self._thread = None
                self._state = "failed" if error else "idle"

    # -------------------------------------------------------------------- files
    def list_files(self, session_id):
        target = (self.root / session_id).resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError("非法会话路径")
        if not target.is_dir():
            raise ValueError("会话不存在")
        return [{"path": item.relative_to(target).as_posix(), "bytes": item.stat().st_size}
                for item in sorted(target.rglob("*")) if item.is_file()]

    def read_file(self, session_id, relative):
        base = (self.root / session_id).resolve()
        if self.root.resolve() not in base.parents or not base.is_dir():
            raise ValueError("非法会话路径")
        target = (base / relative).resolve()
        if base not in target.parents or not target.is_file():
            raise ValueError("非法文件路径")
        return target


class CaptureHandler(BaseHTTPRequestHandler):
    service: CaptureService = None  # 由 serve() 注入
    server_version = "na70-capture/1.0"
    MAX_BODY = 64 * 1024

    def log_message(self, *_args):  # 静音：日志由事件流记录
        pass

    # ---------------------------------------------------------------- responses
    def _send(self, status, payload=None, content_type="application/json; charset=utf-8", raw=None):
        body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > self.MAX_BODY:
            raise ValueError("请求体过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    # --------------------------------------------------------------------- GET
    def do_GET(self):
        url = urlsplit(self.path)
        service = self.service
        if url.path in ("/", "/index.html"):
            return self._send(200, raw=PAGE.read_bytes(), content_type="text/html; charset=utf-8")
        if url.path == "/api/status":
            return self._send(200, service.status())
        if url.path == "/api/sessions":
            return self._send(200, {"sessions": service.sessions()})
        if url.path == "/api/files":
            query = parse_qs(url.query)
            session_id = (query.get("session") or [""])[0]
            try:
                return self._send(200, {"files": service.list_files(session_id)})
            except ValueError as exc:
                return self._error(400, str(exc))
        if url.path == "/api/file":
            query = parse_qs(url.query)
            session_id = (query.get("session") or [""])[0]
            relative = (query.get("path") or [""])[0]
            try:
                target = service.read_file(session_id, relative)
            except ValueError as exc:
                return self._error(400, str(exc))
            return self._send(200, raw=target.read_bytes(), content_type="application/octet-stream")
        return self._error(404, "未知路径")

    # -------------------------------------------------------------------- POST
    def do_POST(self):
        if not self._same_origin():
            return self._error(403, "跨站请求被拒绝")
        url = urlsplit(self.path)
        service = self.service
        try:
            payload = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self._error(400, f"请求体不是合法 JSON：{exc}")
        if url.path == "/api/session/start":
            task = str(payload.get("task") or "").strip()
            duration = payload.get("duration_s")
            if not task:
                return self._error(400, "缺少 task")
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                return self._error(400, "duration_s 必须是数字")
            if not 3 <= duration <= 1800:
                return self._error(400, "duration_s 需在 3–1800 秒之间")
            operator = payload.get("operator", {})
            if operator is None:
                operator = {}
            if not isinstance(operator, dict):
                return self._error(400, "operator 必须是对象")
            try:
                return self._send(202, service.start(task=task, duration_s=duration,
                                                     operator=operator,
                                                     note=str(payload.get("note") or "")))
            except RuntimeError as exc:
                return self._error(409, str(exc))
        if url.path == "/api/session/stop":
            return self._send(202, service.request_stop())
        return self._error(404, "未知路径")


def serve(service, host="127.0.0.1", port=8791):
    handler = type("BoundCaptureHandler", (CaptureHandler,), {"service": service})
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd


def run_server(service, httpd):
    """On normal termination stop active capture before exiting the server."""
    previous = {}
    shutting_down = threading.Event()

    def stop_server(_signum, _frame):
        if not shutting_down.is_set():
            shutting_down.set()
            service.request_stop()
            threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, stop_server)
    try:
        httpd.serve_forever()
    finally:
        service.request_stop()
        service.wait()
        httpd.server_close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="NA70+IMU 采集控制台（仅 127.0.0.1）")
    parser.add_argument("--root", default=str(Path.home() / "na70-sessions"))
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--imu-port", default=None)
    parser.add_argument("--imu-baud", type=int, default=460800)
    parser.add_argument("--lidar-iface", default="enP8p1s0")
    parser.add_argument("--run-as", default=None)
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    args = parser.parse_args(argv)
    service = CaptureService(args.root, imu_port=args.imu_port, imu_baud=args.imu_baud,
                             lidar_iface=args.lidar_iface, run_as=args.run_as,
                             min_free_bytes=int(args.min_free_gb * 1024 ** 3))
    httpd = serve(service, port=args.port)
    print(f"NA70 capture console: http://127.0.0.1:{args.port}  sessions={args.root}", flush=True)
    run_server(service, httpd)


if __name__ == "__main__":
    main()
