"""Local-only YESENSE serial monitor. Does not send device commands."""
import argparse
import collections
import csv
import io
import hashlib
import json
import pathlib
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import serial
from calibration import evaluate_static

ROOT = pathlib.Path(__file__).resolve().parent


class Parser:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_checksum = 0
        self.bad_payload = 0

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 7:
            start = self.buffer.find(b'YS')
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 7:
                break
            size = self.buffer[4] + 7
            if len(self.buffer) < size:
                break
            raw = bytes(self.buffer[:size])
            a = b = 0
            for value in raw[2:-2]:
                a = (a + value) & 255
                b = (b + a) & 255
            if raw[-2:] != bytes((a, b)):
                self.bad_checksum += 1
                del self.buffer[0]
                continue
            del self.buffer[:size]
            fields = {}
            payload = raw[5:-2]
            pos = 0
            malformed = False
            specs = {0x10: ('accel', 12), 0x20: ('gyro', 12),
                     0x40: ('euler', 12), 0x41: ('quaternion', 16)}
            while pos < len(payload):
                if pos + 2 > len(payload):
                    malformed = True
                    break
                kind, length = payload[pos:pos + 2]
                value = payload[pos + 2:pos + 2 + length]
                if len(value) != length:
                    malformed = True
                    break
                if kind in specs:
                    name, expected = specs[kind]
                    if length != expected:
                        malformed = True
                        break
                    fields[name] = [v * 1e-6 for v in struct.unpack('<' + 'i' * (length // 4), value)]
                elif kind in (0x51, 0x52) and length == 4:
                    fields['sample_us' if kind == 0x51 else 'ready_us'] = int.from_bytes(value, 'little')
                pos += length + 2
            if malformed:
                self.bad_payload += 1
                continue
            frames.append({'tid': int.from_bytes(raw[2:4], 'little'),
                           'fields': fields, 'hex': raw.hex(' ').upper()})
        return frames


class Monitor:
    def __init__(self, port, baud):
        self.port, self.baud = port, baud
        self.lock = threading.Lock()
        self.rows = collections.deque(maxlen=6000)
        self.arrivals = collections.deque(maxlen=2000)
        self.started = time.monotonic()
        self.count = self.byte_count = self.bad_checksum = self.bad_payload = 0
        self.gaps = self.reconnects = 0
        self.last_tid = self.last_time = None
        self.status = 'connecting'
        self.error = ''
        self.calibration_job = {'status': 'idle'}
        threading.Thread(target=self.collect, daemon=True).start()

    def collect(self):
        while True:
            parser = Parser()
            try:
                with serial.Serial(self.port, self.baud, timeout=0.05, write_timeout=0.1) as device:
                    with self.lock:
                        self.status, self.error = 'waiting', ''
                        self.last_tid = None
                    while True:
                        data = device.read(min(max(device.in_waiting, 1), 8192))
                        now = time.monotonic()
                        frames = parser.feed(data)
                        with self.lock:
                            self.byte_count += len(data)
                            self.bad_checksum += parser.bad_checksum
                            self.bad_payload += parser.bad_payload
                            parser.bad_checksum = parser.bad_payload = 0
                            for frame in frames:
                                tid = frame['tid']
                                if self.last_tid is not None and tid not in ((self.last_tid + 1) & 65535, 1 if self.last_tid == 60000 else -1):
                                    self.gaps += 1
                                self.last_tid, self.last_time = tid, now
                                self.count += 1
                                frame.update(id=self.count, t=now - self.started, host_time=time.time())
                                self.rows.append(frame)
                                self.arrivals.append(now)
                            self.status = 'live' if self.last_time is not None and now - self.last_time < 1 else 'waiting'
            except (serial.SerialException, OSError) as exc:
                with self.lock:
                    self.status, self.error = 'disconnected', str(exc)
                    self.reconnects += 1
                time.sleep(1)

    def start_static_check(self):
        with self.lock:
            if self.calibration_job['status'] == 'collecting':
                return False
            first_id = self.count
            initial_errors = self.bad_checksum + self.bad_payload + self.reconnects
            started = time.monotonic()
            self.calibration_job = {'status': 'collecting', 'started_host': time.time(), 'progress': 0}
        def run():
            for tick in range(100):
                time.sleep(0.1)
                with self.lock:
                    self.calibration_job['progress'] = tick + 1
            with self.lock:
                rows = [r for r in self.rows if r['id'] > first_id]
                errors = self.bad_checksum + self.bad_payload + self.reconnects - initial_errors
            result = evaluate_static(rows, errors)
            result.update(port=self.port, baud=self.baud, capture_elapsed_s=time.monotonic()-started)
            try:
                evidence = ROOT / 'evidence'
                evidence.mkdir(exist_ok=True)
                tag = time.strftime('%Y%m%d-%H%M%S')
                raw = ''.join(json.dumps(row) + '\n' for row in rows).encode()
                source_name = f'static-{tag}.jsonl'
                (evidence / source_name).write_bytes(raw)
                result.update(source_file=source_name, source_sha256=hashlib.sha256(raw).hexdigest())
                (evidence / f'static-{tag}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            except OSError as exc:
                result.update(persistence_error=str(exc))
            with self.lock:
                self.calibration_job = {'status': 'complete', 'progress': 100, 'result': result}
        threading.Thread(target=run, daemon=True).start()
        return True

    def snapshot(self, after):
        with self.lock:
            now = time.monotonic()
            recent = [t for t in self.arrivals if t >= now - 2]
            span = min(2, now - self.started)
            return {'port': self.port, 'baud': self.baud, 'status': self.status,
                    'error': self.error, 'frames': self.count, 'bytes': self.byte_count,
                    'checksum_errors': self.bad_checksum, 'payload_errors': self.bad_payload,
                    'sequence_breaks': self.gaps, 'reconnects': self.reconnects,
                    'hz': len(recent) / span if span else 0,
                    'age_ms': (now - self.last_time) * 1000 if self.last_time else None,
                    'uptime_s': now - self.started,
                    'latest': self.rows[-1] if self.rows else None,
                    'rows': [row for row in self.rows if row['id'] > after][-2400:]}


def serve(monitor, http_port):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            # No cross-origin command requests; this affects host-side recording only.
            origin = self.headers.get('Origin')
            if origin and origin != f'http://127.0.0.1:{http_port}':
                self.send_error(403)
                return
            if self.path != '/api/calibration/start':
                self.send_error(404)
                return
            ok = monitor.start_static_check()
            self.send_response(202 if ok else 409)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path in ('/api/calibration', '/api/calibration.json'):
                with monitor.lock:
                    content = json.dumps(monitor.calibration_job).encode()
                mime = 'application/json'
            elif url.path == '/api/data':
                try:
                    after = max(0, int(parse_qs(url.query).get('after', ['0'])[0]))
                except ValueError:
                    self.send_error(400, 'Invalid cursor')
                    return
                content = json.dumps(monitor.snapshot(after)).encode()
                mime = 'application/json'
            elif url.path == '/api/export.csv':
                with monitor.lock:
                    rows = list(monitor.rows)
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['id', 'host_unix_s', 'host_elapsed_s', 'tid', 'sample_us',
                                 'ax_m_s2', 'ay_m_s2', 'az_m_s2', 'gx_deg_s', 'gy_deg_s', 'gz_deg_s',
                                 'pitch_deg', 'roll_deg', 'yaw_deg', 'qw', 'qx', 'qy', 'qz'])
                for row in rows:
                    f = row['fields']
                    writer.writerow([row['id'], row['host_time'], row['t'], row['tid'], f.get('sample_us', '')]
                                    + f.get('accel', [''] * 3) + f.get('gyro', [''] * 3)
                                    + f.get('euler', [''] * 3) + f.get('quaternion', [''] * 4))
                content = ('\ufeff' + output.getvalue()).encode('utf-8')
                mime = 'text/csv; charset=utf-8'
            elif url.path in ('/', '/index.html'):
                content = (ROOT / 'index.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif url.path in ('/model', '/model.html', '/model.js'):
                name = 'model.js' if url.path.endswith('.js') else 'model.html'
                content = (ROOT / name).read_bytes()
                mime = 'application/javascript; charset=utf-8' if name.endswith('.js') else 'text/html; charset=utf-8'
            elif url.path in ('/photos/top.png', '/photos/front.png', '/photos/side.png'):
                content = (ROOT / url.path.lstrip('/')).read_bytes()
                mime = 'image/png'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(content)))
            if url.path.endswith('.csv'):
                self.send_header('Content-Disposition', 'attachment; filename="yesense-recent.csv"')
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', http_port), Handler)
    print(f'YESENSE monitor: http://127.0.0.1:{http_port} | {monitor.port} @ {monitor.baud}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--port', default='COM12')
    cli.add_argument('--baud', type=int, default=460800)
    cli.add_argument('--http-port', type=int, default=8770)
    args = cli.parse_args()
    serve(Monitor(args.port, args.baud), args.http_port)
