"""A2 采集入口服务测试：状态机、HTTP 契约、路径安全与页面可用性（不接真设备）。"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from na70_core.capture.service import CaptureService, serve
from na70_core.capture.lock import CaptureLock


class FakeRunner:
    """替代真实 runner：不碰串口/网卡，只产生会话文件与可预期的状态。"""

    def __init__(self, session, kind="ok"):
        self.session = session
        self.kind = kind
        self.started = False
        self.payload = {"state": "recording",
                        "imu": {"state": "live", "hz": 199.4, "frames": 12, "checksum_errors": 0,
                                "sequence_breaks": 0, "queue_overflow": 0, "reconnects": 0},
                        "lidar": {"state": "live", "pcap_bytes": 4096, "dropped": 0}}

    def status(self):
        return dict(self.payload)

    def start(self):
        self.started = True

    def _safe_stop(self):
        self.started = False

    def run(self, seconds, tick_interval=1.0, should_stop=None):
        started = time.monotonic()
        self.session.event("fake_run_start", seconds=seconds)
        while time.monotonic() - started < seconds:
            if should_stop is not None and should_stop():
                break
            time.sleep(0.02)
        elapsed = round(time.monotonic() - started, 3)
        return self.session.finalize(
            status=self.kind,
            quality={"status": self.kind, "duration_s": elapsed, "overlap_s": elapsed,
                     "checks": [{"name": "imu_frames", "ok": True, "detail": "12 帧"}]},
            devices={}, windows={})


@pytest.fixture()
def server(tmp_path):
    service = CaptureService(tmp_path / "sessions",
                             runner_factory=lambda session: FakeRunner(session),
                             min_free_bytes=0, tick_interval=0.05)
    httpd = serve(service, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]

    def get(path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post(path, payload, headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    yield service, get, post, port
    httpd.shutdown()
    httpd.server_close()


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_page_is_served(server):
    _service, get, _post, _port = server
    status, body = get("/")
    assert status == 200
    text = body.decode("utf-8")
    assert "采集台" in text
    assert "/api/session/start" in text


def test_start_records_and_finalizes(server):
    service, get, post, _port = server
    status, data = post("/api/session/start", {"task": "B1", "duration_s": 3,
                                               "operator": {"wall_distance_m": 5.02},
                                               "note": "unit-test"})
    assert status == 202
    session_id = data["session_id"]
    assert wait_for(lambda: service.wait(timeout=0)["state"] == "idle")
    payload = json.loads(get("/api/status")[1])
    assert payload["last"]["session_id"] == session_id
    assert payload["last"]["status"] == "ok"
    session_dir = service.root / session_id
    assert (session_dir / "manifest.json").exists()
    assert (session_dir / "quality.json").exists()
    operator = json.loads((session_dir / "operator.json").read_text("utf-8"))
    assert operator["operator"]["wall_distance_m"] == pytest.approx(5.02)
    assert operator["operator"]["note"] == "unit-test"


def test_second_start_is_rejected(server):
    _service, _get, post, _port = server
    assert post("/api/session/start", {"task": "B1", "duration_s": 30})[0] == 202
    status, body = post("/api/session/start", {"task": "B1", "duration_s": 30})
    assert status == 409
    assert "会话" in body["error"]


def test_stop_finishes_early(server):
    service, _get, post, _port = server
    assert post("/api/session/start", {"task": "B2", "duration_s": 60})[0] == 202
    time.sleep(0.3)
    assert post("/api/session/stop", {})[0] == 202
    assert wait_for(lambda: service.wait(timeout=0)["state"] == "idle")
    assert service.status()["last"]["duration_s"] < 10


def test_validation_errors(server):
    _service, _get, post, _port = server
    assert post("/api/session/start", {"duration_s": 10})[0] == 400
    assert post("/api/session/start", {"task": "B1", "duration_s": "abc"})[0] == 400
    assert post("/api/session/start", {"task": "B1", "duration_s": 1})[0] == 400
    assert post("/api/session/start", {"task": "B1", "duration_s": 10, "operator": []})[0] == 400
    assert post("/api/unknown", {})[0] == 404


def test_cross_origin_post_is_rejected(server):
    _service, _get, post, _port = server
    status, _body = post("/api/session/start", {"task": "B1", "duration_s": 10},
                         headers={"Origin": "http://evil.example"})
    assert status == 403


def test_file_listing_and_traversal_guard(server):
    service, get, post, _port = server
    assert post("/api/session/start", {"task": "B1", "duration_s": 3})[0] == 202
    assert wait_for(lambda: service.wait(timeout=0)["state"] == "idle")
    session_id = service.status()["last"]["session_id"]
    status, body = get(f"/api/files?session={session_id}")
    assert status == 200
    paths = {item["path"] for item in json.loads(body)["files"]}
    assert "manifest.json" in paths
    assert "../" not in "".join(paths)
    assert get(f"/api/files?session=..%2F..%2Fetc")[0] == 400
    assert get(f"/api/file?session={session_id}&path=../../etc/passwd")[0] == 400
    assert get("/api/file?session=..%2F..&path=AGENTS.md")[0] == 400


def test_sessions_listing_after_finish(server):
    service, get, post, _port = server
    assert post("/api/session/start", {"task": "B3", "duration_s": 3})[0] == 202
    assert wait_for(lambda: service.wait(timeout=0)["state"] == "idle")
    sessions = json.loads(get("/api/sessions")[1])["sessions"]
    assert sessions and sessions[0]["task"] == "B3"
    assert sessions[0]["status"] == "ok"


def test_unfinalized_session_survives_service_restart(tmp_path):
    session = tmp_path / "20261030-001"
    session.mkdir()
    (session / "operator.json").write_text(json.dumps({"operator": {"task": "B1"}}), encoding="utf-8")
    (session / "events.jsonl").write_text('{"kind":"session_created"}\n', encoding="utf-8")
    service = CaptureService(tmp_path, min_free_bytes=0)
    entry = service.sessions()[0]
    assert entry["session_id"] == session.name
    assert entry["status"] == "unfinalized"
    assert entry["task"] == "B1"
    assert {item["path"] for item in service.list_files(session.name)} == {"operator.json", "events.jsonl"}
    (session / "manifest.json").write_text('{broken', encoding="utf-8")
    assert service.sessions()[0]["status"] == "unfinalized"


def test_killed_synthetic_recorder_leaves_visible_session_and_releases_lock(tmp_path):
    code = (
        "import sys,time; from pathlib import Path; "
        "from na70_core.capture.session import CaptureSession; "
        "from na70_core.capture.lock import CaptureLock; "
        "root=Path(sys.argv[1]); lock=CaptureLock(root); lock.acquire(); "
        "session=CaptureSession(root,session_id='killed',task='SYNTHETIC'); "
        "session.file('raw/imu.bin').write_bytes(b'raw-data'); "
        "print('READY',flush=True); time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-u", "-c", code, str(tmp_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == b"READY"
    finally:
        child.kill()
        child.communicate(timeout=10)
    entry = CaptureService(tmp_path, min_free_bytes=0).sessions()[0]
    assert entry["status"] == "unfinalized" and entry["task"] == "SYNTHETIC"
    assert (tmp_path / "killed/raw/imu.bin").read_bytes() == b"raw-data"
    assert not (tmp_path / "killed/manifest.json").exists()
    new_lock = CaptureLock(tmp_path)
    new_lock.acquire()
    new_lock.release()


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate does not deliver SIGTERM to Python")
def test_sigterm_finalizes_active_synthetic_capture(tmp_path):
    code = '''
import sys, time
from na70_core.capture.service import CaptureService, serve, run_server
class Runner:
    def __init__(self, session): self.session = session
    def start(self): pass
    def status(self): return {}
    def run(self, seconds, tick_interval=1, should_stop=None):
        while not should_stop(): time.sleep(0.01)
        return self.session.finalize(status="degraded", quality={"status":"degraded", "duration_s":1, "checks":[]})
service = CaptureService(sys.argv[1], runner_factory=Runner, min_free_bytes=0)
server = serve(service, port=0)
service.start(task="SYNTHETIC", duration_s=60)
print(server.server_address[1], flush=True)
run_server(service, server)
'''
    child = subprocess.Popen([sys.executable, "-u", "-c", code, str(tmp_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        port = int(child.stdout.readline())
        def ready():
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as response:
                    return response.status == 200
            except OSError:
                return False
        assert wait_for(ready)
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=10) == 0
        manifests = list(tmp_path.glob("*/manifest.json"))
        assert len(manifests) == 1
        assert json.loads(manifests[0].read_text(encoding="utf-8"))["status"] == "degraded"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_failed_runner_keeps_session_visible(tmp_path):
    service = CaptureService(tmp_path / "sessions", min_free_bytes=0,
                             runner_factory=lambda session: BrokenRunner(session))
    service.start(task="B5", duration_s=3)
    assert wait_for(lambda: service.status()["state"] == "failed")
    assert service.status()["error"]
    assert (service.root / service.status()["session_id"]).is_dir(), "失败会话目录必须保留"
    assert (service.root / service.status()["session_id"] / "manifest.json").exists()


def test_stop_during_start_is_honored(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class SlowRunner(FakeRunner):
        def start(self):
            entered.set()
            assert release.wait(5)
            super().start()

    service = CaptureService(tmp_path, min_free_bytes=0, runner_factory=SlowRunner)
    service.start(task="B1", duration_s=60)
    assert entered.wait(5)
    assert service.request_stop()["state"] == "stopping"
    with pytest.raises(RuntimeError):
        service.start(task="B1", duration_s=3)
    release.set()
    assert wait_for(lambda: service.status()["state"] == "idle")
    assert service.status()["last"]["duration_s"] < 1


class BrokenRunner(FakeRunner):
    def run(self, seconds, tick_interval=1.0, should_stop=None):
        raise RuntimeError("模拟设备掉线")
