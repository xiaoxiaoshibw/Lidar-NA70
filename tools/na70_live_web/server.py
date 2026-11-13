"""Local-only NA70 live viewer. Run on the receiving Jetson, use SSH tunneling."""
import json
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
from na70_core.geometry import points_from_codes

ROOT = Path(__file__).resolve().parent
lock = threading.Lock()
state = {"packets": 0, "frames": 0, "last_packet": None, "points": 0, "error": "", "pitch_offset": 0.0, "yaw_offset": 0.0}
frames = deque(maxlen=30)


def receive():
    batch = []
    pitch = yaw = 0.0
    last_arrival = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            sock.bind(("0.0.0.0", 2368))
            while True:
                data, _ = sock.recvfrom(65535)
                if len(data) != 1410:
                    continue
                now = time.monotonic()
                if last_arrival is not None and now - last_arrival > 1.0:
                    batch.clear()
                    with lock:
                        frames.clear()
                        state["buffered_frames"] = 0
                last_arrival = now
                factory = int.from_bytes(data[1408:1410], "little")
                if factory >> 12 == 1:
                    pitch = (factory & 4095) * 0.01 - 20.48
                elif factory >> 12 == 2:
                    yaw = (factory & 4095) * 0.01 - 20.48
                batch.append(data[:1400])
                with lock:
                    state["packets"] += 1
                    state["last_packet"] = time.time()
                if len(batch) < 100:
                    continue
                raw = np.frombuffer(b"".join(batch), dtype=np.uint8).reshape(-1, 7)
                codes = np.empty((len(raw), 4), dtype=np.uint16)
                for i in range(3):
                    codes[:, i] = (raw[:, 2*i].astype(np.uint16) << 8) | raw[:, 2*i+1]
                codes[:, 3] = raw[:, 6]
                codes = codes[codes[:, 0] != 0]
                points = points_from_codes(codes, pitch, yaw)
                with lock:
                    frames.append(points.tobytes())
                    state.update(frames=state["frames"] + 1, buffered_frames=len(frames), points=len(points), pitch_offset=pitch, yaw_offset=yaw)
                batch.clear()
    except Exception as exc:
        with lock:
            state["error"] = str(exc)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/status":
            with lock:
                snapshot = dict(state)
            snapshot["age_seconds"] = None if snapshot["last_packet"] is None else max(0, time.time() - snapshot["last_packet"])
            body, content_type = json.dumps(snapshot).encode(), "application/json"
        elif url.path == "/api/frame":
            try:
                count = int(parse_qs(url.query).get("frames", ["1"])[0])
                if not 1 <= count <= 30:
                    raise ValueError()
            except ValueError:
                self.send_error(400, "frames must be an integer from 1 to 30")
                return
            with lock:
                body = b"".join(list(frames)[-count:])
            content_type = "application/octet-stream"
        else:
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/three.module.js": ("three.module.js", "text/javascript"), "/three.core.js": ("three.core.js", "text/javascript"), "/OrbitControls.js": ("OrbitControls.js", "text/javascript")}
            if url.path not in files:
                self.send_error(404)
                return
            filename, content_type = files[url.path]
            body = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=receive, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8795), Handler).serve_forever()
