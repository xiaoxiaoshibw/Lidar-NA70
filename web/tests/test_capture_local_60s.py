"""SYNTHETIC: the double-click script talks to a fake local service."""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize(("capture_status", "expected_exit"), [("degraded", 0), ("failed", 1)])
def test_shortcut_waits_for_its_session_and_reports_path(capture_status, expected_exit):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_json(self, payload, status=200):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_json({"session_id": "synthetic", "state": "starting"}, 202)

        def do_GET(self):
            if not requests:
                self.send_json({"state": "idle", "sessions": []})
            else:
                self.send_json({"state": "idle", "sessions": [], "session_id": "synthetic",
                                "last": {"session_id": "synthetic", "status": capture_status,
                                         "path": "synthetic-session", "failed_checks": []}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = Path(__file__).resolve().parents[2] / "tools" / "capture_local_60s.ps1"
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-Port", str(server.server_port)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert "synthetic-session" in result.stdout
    assert capture_status in result.stdout
    assert requests == [{"task": "MANUAL-60S", "duration_s": 60,
                         "imu_port": "COM12", "imu_baud": 460800}]
