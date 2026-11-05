"""A1 采集层测试：IMU 帧解析、会话落盘、写队列/重连、tcpdump 封装、质量判定。"""

import json
import importlib.util
import queue
import subprocess
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

from na70_core.capture import (CaptureRunner, CaptureSession, CaptureSessionError, FrameParser,
                               ImuFrame, ImuRecorder, LidarCapture, next_tid, sha256_file)
from na70_core.capture.lidar import LidarError
from na70_core.capture.lock import CaptureLock
from na70_core.capture.yesense import tid_follows


# --------------------------------------------------------------------------- helpers
def build_frame(tid, accel=(0, 0, 9800000), gyro=(0, 0, 0), euler=(1000000, 2000000, 3000000),
                quat=(1000000, 0, 0, 0), include_quat=True, sample_us=None):
    payload = b""
    payload += bytes((0x10, 12)) + struct.pack("<3i", *accel)
    payload += bytes((0x20, 12)) + struct.pack("<3i", *gyro)
    payload += bytes((0x40, 12)) + struct.pack("<3i", *euler)
    if include_quat:
        payload += bytes((0x41, 16)) + struct.pack("<4i", *quat)
    if sample_us is not None:
        payload += bytes((0x51, 4)) + struct.pack("<I", sample_us)
    body = struct.pack("<H", tid & 0xFFFF) + bytes((len(payload),)) + payload
    a = b = 0
    for value in body:
        a = (a + value) & 0xFF
        b = (b + a) & 0xFF
    return b"YS" + body + bytes((a, b))


class FakeSerial:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size):
        if self.chunks:
            result, self.chunks[0] = self.chunks[0][:size], self.chunks[0][size:]
            if not self.chunks[0]:
                self.chunks.pop(0)
            return result
        time.sleep(0.005)
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True
        return False


class FullQueue:
    """永远满的写队列：用于验证溢出会计数而不是静默丢帧。"""

    def __init__(self):
        self.done = False

    def put_nowait(self, _item):
        raise queue.Full

    def put(self, item, timeout=None):
        assert item is None
        self.done = True

    def get(self, timeout=None):  # noqa: ARG002
        if self.done:
            return None
        time.sleep(0.005)
        raise queue.Empty


class FlakySerial(FakeSerial):
    """读到第 N 次后抛 OSError，模拟线缆松动/设备复位。"""

    def __init__(self, chunks, fail_after):
        super().__init__(chunks)
        self.fail_after = fail_after
        self.reads = 0

    def read(self, size):
        self.reads += 1
        if self.reads > self.fail_after:
            raise OSError("device disappeared")
        return super().read(size)


class FakeProcess:
    def __init__(self, pcap_path, stats="120 packets captured\n121 packets received by filter\n0 packets dropped by kernel\n"):
        self.pcap_path = pcap_path
        self.stats = stats
        self.exited = False
        self.signal = None

    def poll(self):
        return 0 if self.exited else None

    def send_signal(self, sig):
        self.signal = sig
        self.exited = True
        with open(str(self.pcap_path) + "", "ab") as handle:
            handle.write(b"x" * 16)
        log = str(self.pcap_path).replace("lidar.pcap", "lidar-tcpdump.log")
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(self.stats)

    def wait(self, timeout=None):  # noqa: ARG002
        return 0

    def terminate(self):
        self.exited = True

    def kill(self):
        self.exited = True


# --------------------------------------------------------------------------- parser
def test_tid_accepts_observed_device_wrap_without_masking_other_gaps():
    assert tid_follows(60000, 1)
    assert tid_follows(65535, 0)
    assert not tid_follows(59999, 1)
    assert not tid_follows(60000, 2)


def test_frame_parser_decodes_all_groups():
    frame = build_frame(7, accel=(1500000, -2250000, 9800000), gyro=(100000, -200000, 30000))
    [parsed] = FrameParser().feed(frame)
    assert parsed.tid == 7
    assert parsed.accel == pytest.approx((1.5, -2.25, 9.8), abs=1e-9)
    assert parsed.gyro == pytest.approx((0.1, -0.2, 0.03), abs=1e-9)
    assert parsed.sample_us is None and parsed.ready_us is None
    assert parsed.raw == frame


def test_frame_parser_survives_bytewise_chunks():
    frames = [build_frame(i) for i in range(5)]
    stream = b"".join(frames)
    parser = FrameParser()
    out = []
    for index in range(0, len(stream), 3):
        out.extend(parser.feed(stream[index:index + 3]))
    assert [frame.tid for frame in out] == [0, 1, 2, 3, 4]


def test_frame_parser_records_bad_checksum_and_resyncs():
    good = build_frame(1)
    broken = bytearray(build_frame(2))
    broken[-1] ^= 0xFF
    parser = FrameParser()
    frames = parser.feed(bytes(broken) + good)
    assert parser.bad_checksum == 1
    assert [frame.tid for frame in frames] == [1]


def test_frame_parser_counts_garbage_and_keeps_partial_frame():
    parser = FrameParser()
    assert parser.feed(b"garbage!") == []
    assert parser.discarded_bytes == 7
    frame = build_frame(3)
    assert parser.feed(frame[:-2]) == []
    assert parser.pending_bytes == len(frame) - 2
    assert [f.tid for f in parser.feed(frame[-2:])] == [3]


def test_frame_parser_rejects_wrong_group_length():
    payload = bytes((0x10, 8)) + struct.pack("<2i", 1, 2)
    body = struct.pack("<H", 9) + bytes((len(payload),)) + payload
    a = b = 0
    for value in body:
        a = (a + value) & 0xFF
        b = (b + a) & 0xFF
    parser = FrameParser()
    assert parser.feed(b"YS" + body + bytes((a, b))) == []
    assert parser.bad_payload == 1


def test_next_tid_wraps():
    assert next_tid(65535) == 0
    assert next_tid(41) == 42


# --------------------------------------------------------------------------- session
def test_session_is_exclusive_and_hashed(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="run-1", operator={"notes": ["x"]})
    with session.open("raw/imu.bin", "ab") as handle:
        handle.write(b"abc")
    manifest = session.finalize(status="ok", quality={"status": "ok"})
    assert manifest["session_id"] == "run-1"
    assert manifest["files"], "manifest 必须列出文件哈希"
    entry = next(item for item in manifest["files"] if item["path"] == "raw/imu.bin")
    assert entry["bytes"] == 3
    assert entry["sha256"] == sha256_file(session.path / "raw" / "imu.bin")
    assert all(entry["sha256"] == sha256_file(session.path / entry["path"]) for entry in manifest["files"])
    assert (session.path / "quality.json").exists()
    assert (session.path / "operator.json").exists()
    assert all(item["path"] != "manifest.json" for item in manifest["files"])
    with pytest.raises(CaptureSessionError):
        CaptureSession(tmp_path / "sessions", session_id="run-1")


def test_session_disk_guard(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="disk")
    session.min_free_bytes = 1 << 62
    with pytest.raises(CaptureSessionError):
        session.ensure_disk()
    session.min_free_bytes = 0
    assert session.ensure_disk() > 0


# --------------------------------------------------------------------------- imu
def test_imu_recorder_writes_raw_frames_and_host_csv(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="imu-1")
    stream = b"".join(build_frame(i) for i in range(30))
    recorder = ImuRecorder(session, "fake", serial_factory=lambda _p, _b: FakeSerial([stream]),
                           flush_interval=0.05, rate_window=0.5)
    recorder.start()
    deadline = time.monotonic() + 3
    while recorder.frames < 30 and time.monotonic() < deadline:
        time.sleep(0.01)
    status = recorder.stop()
    assert status["frames"] == 30
    assert status["bytes"] == len(stream)
    assert status["checksum_errors"] == 0
    assert status["sequence_breaks"] == 0
    assert (session.path / "raw" / "imu.bin").read_bytes() == stream
    chunks = [json.loads(line) for line in (session.path / "raw" / "imu_chunks.jsonl").read_text("utf-8").splitlines()]
    assert sum(chunk["bytes"] for chunk in chunks) == len(stream)
    assert chunks[0]["offset"] == 0
    csv_lines = (session.path / "imu_host_receive.csv").read_text("utf-8").splitlines()
    assert csv_lines[0].startswith("host_monotonic_s,host_unix_s,tid,segment")
    assert "device_time_s" not in csv_lines[0], "无设备时间时不得伪造 device_time_s 列"
    assert len(csv_lines) == 31
    frames = [json.loads(line) for line in (session.path / "imu_frames.jsonl").read_text("utf-8").splitlines()]
    assert len(frames) == 30
    events = [json.loads(line) for line in (session.path / "events.jsonl").read_text("utf-8").splitlines()]
    started = next(event for event in events if event["kind"] == "imu_started")
    assert started["monotonic_clock"] == "perf_counter"
    assert started["wall_clock"] == "time"
    assert all(frame["t_mono"] == chunks[0]["t_mono"] for frame in frames)
    assert frames[0]["accel"] == pytest.approx([0.0, 0.0, 9.8])
    # 停机后仍要报告整段平均频率，不能因为“最近 2 秒”为空就变成 0 Hz
    assert status["hz"] > 0


def test_imu_reads_available_bytes_when_driver_waits_for_full_request(tmp_path):
    # SYNTHETIC: model a driver that ignores its timeout and emits 67 B/5 ms.
    class BlockingSerial:
        def __init__(self):
            self.started = time.monotonic()
            self.offset = 0
            self.stream = b"".join(build_frame(i) for i in range(200))
            self.cancelled = threading.Event()

        @property
        def in_waiting(self):
            produced = min(len(self.stream), int((time.monotonic() - self.started) / .005) * 67)
            return max(0, produced - self.offset)

        def read(self, size):
            while self.in_waiting < size and not self.cancelled.wait(.001):
                pass
            count = min(size, self.in_waiting)
            data = self.stream[self.offset:self.offset + count]
            self.offset += count
            return data

        def cancel_read(self):
            self.cancelled.set()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    old = BlockingSerial()
    started = time.monotonic()
    assert len(old.read(8192)) == 8192
    assert time.monotonic() - started >= .5

    serial = BlockingSerial()
    session = CaptureSession(tmp_path / "sessions", session_id="imu-latency")
    recorder = ImuRecorder(session, "fake", serial_factory=lambda *_: serial, flush_interval=.05)
    recorder.start()
    deadline = time.monotonic() + 2
    while recorder.frames < 40 and time.monotonic() < deadline:
        time.sleep(.005)
    status = recorder.stop()
    chunks = [json.loads(line) for line in (session.path / "raw/imu_chunks.jsonl").read_text("utf-8").splitlines()]
    assert status["frames"] >= 40
    assert len(chunks) > 20
    assert max(chunk["bytes"] for chunk in chunks) < 8192
    assert (session.path / "raw/imu.bin").read_bytes() == serial.stream[:status["bytes"]]
    assert status["worker_error"] == ""


def test_imu_stop_cancels_blocking_read_and_saves_returned_tail(tmp_path):
    class CancelledSerial(FakeSerial):
        def __init__(self):
            super().__init__([])
            self.reading = threading.Event()
            self.cancelled = threading.Event()

        def read(self, size):
            assert size == 1
            self.reading.set()
            assert self.cancelled.wait(2)
            return b"Y"  # a byte delivered with cancellation must still be saved

        def cancel_read(self):
            self.cancelled.set()

    serial = CancelledSerial()
    session = CaptureSession(tmp_path / "sessions", session_id="imu-stop")
    recorder = ImuRecorder(session, "fake", serial_factory=lambda *_: serial)
    recorder.start()
    assert serial.reading.wait(2)
    status = recorder.stop(timeout=2)
    assert status["bytes"] == 1
    assert status["discarded_bytes"] == 1
    assert status["reconnects"] == 0
    assert (session.path / "raw/imu.bin").read_bytes() == b"Y"


def test_runner_elapsed_uses_imu_clock_domain(tmp_path):
    session = CaptureSession(tmp_path, session_id="clock-domain")
    recorder = ImuRecorder(session, "fake", clock=lambda: 105.0)
    recorder.started_mono = 100.0
    runner = CaptureRunner(session, imu=recorder, clock=lambda: 10_000.0)
    assert runner.status()["elapsed_s"] == 5.0


def test_imu_recorder_counts_device_sample_time(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="imu-device-time")
    stream = b"".join(build_frame(i, sample_us=1_000_000 + i * 5000) for i in range(5))
    recorder = ImuRecorder(session, "fake", serial_factory=lambda _p, _b: FakeSerial([stream]),
                           flush_interval=0.05)
    recorder.start()
    deadline = time.monotonic() + 3
    while recorder.frames < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    status = recorder.stop()
    assert status["frames"] == 5
    assert status["sample_time_frames"] == 5
    frames = [json.loads(line) for line in (session.path / "imu_frames.jsonl").read_text("utf-8").splitlines()]
    assert [row["sample_us"] for row in frames] == [1_000_000 + i * 5000 for i in range(5)]


def test_imu_recorder_counts_queue_overflow(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="imu-overflow")
    stream = b"".join(build_frame(i) for i in range(5))
    recorder = ImuRecorder(session, "fake", serial_factory=lambda _p, _b: FakeSerial([stream]),
                           flush_interval=0.05)
    recorder._queue = FullQueue()  # noqa: SLF001
    recorder.start()
    deadline = time.monotonic() + 3
    while recorder.frames < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    status = recorder.stop()
    assert status["frames"] == 5
    assert status["queue_overflow"] == 5
    assert (session.path / "raw" / "imu.bin").read_bytes() == stream, "溢出时原始字节仍必须完整"


def test_imu_recorder_rotates_segment_after_disconnect(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="imu-segment")
    first = b"".join(build_frame(i) for i in range(5))
    second = b"".join(build_frame(i) for i in range(5, 10))
    supplies = [FlakySerial([first], fail_after=1), FakeSerial([second])]

    def factory(_port, _baud):
        return supplies.pop(0) if supplies else FakeSerial([])

    recorder = ImuRecorder(session, "fake", serial_factory=factory, reconnect_delay=0.02,
                           flush_interval=0.05)
    recorder.start()
    deadline = time.monotonic() + 5
    while recorder.frames < 10 and time.monotonic() < deadline:
        time.sleep(0.01)
    status = recorder.stop()
    assert status["reconnects"] >= 1
    assert status["segment"] >= 1, "断流重连必须开新 segment"
    assert status["frames"] >= 10
    segments = {json.loads(line)["segment"]
                for line in (session.path / "imu_frames.jsonl").read_text("utf-8").splitlines() if line}
    assert len(segments) >= 2
    events = [json.loads(line)["kind"] for line in (session.path / "events.jsonl").read_text("utf-8").splitlines()]
    assert "imu_read_failed" in events


def test_imu_recorder_records_open_failure(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="imu-open-fail")
    calls = {"n": 0}

    def factory(_port, _baud):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("device busy")
        return FakeSerial([build_frame(1)])

    recorder = ImuRecorder(session, "fake", serial_factory=factory, reconnect_delay=0.02,
                           flush_interval=0.05)
    recorder.start()
    deadline = time.monotonic() + 3
    while recorder.frames < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    status = recorder.stop()
    assert status["reconnects"] >= 2
    assert status["frames"] >= 1
    events = [json.loads(line) for line in (session.path / "events.jsonl").read_text("utf-8").splitlines()]
    assert any(event["kind"] == "imu_open_failed" for event in events)
    assert any(event["kind"] == "imu_segment_open" for event in events)


# --------------------------------------------------------------------------- lidar
def test_lidar_command_uses_sudo_when_unprivileged(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="lidar-sudo")
    capture = LidarCapture(session, "enP8p1s0", euid=1000)
    assert capture.command()[:3] == ["sudo", "-n", "tcpdump"]
    assert capture.command()[-1] == "udp port 2368"


def test_lidar_command_drops_privileges_when_root(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="lidar-root")
    capture = LidarCapture(session, "enP8p1s0", euid=0, run_as="jetson")
    command = capture.command()
    assert command[0] == "tcpdump"
    assert command[1:3] == ["-Z", "jetson"]


def test_lidar_refuses_to_overwrite_pcap(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="lidar-dup")
    pcap = session.file("raw/lidar.pcap")
    pcap.write_bytes(b"old")
    with pytest.raises(LidarError):
        LidarCapture(session, "enP8p1s0", euid=0).start()


def test_lidar_start_stop_parses_kernel_stats(tmp_path):
    session = CaptureSession(tmp_path / "sessions", session_id="lidar-run")
    processes = []

    def popen(command, **_kwargs):
        path = command[command.index("-w") + 1]
        with open(path, "wb") as handle:
            handle.write(b"start")
        proc = FakeProcess(path)
        processes.append(proc)
        return proc

    capture = LidarCapture(session, "enP8p1s0", euid=0, popen=popen, settle=0.1)
    capture.start()
    status = capture.stop()
    assert status["captured"] == 120
    assert status["received"] == 121
    assert status["dropped"] == 0
    assert status["pcap_bytes"] == 21
    events = [json.loads(line) for line in (session.path / "events.jsonl").read_text("utf-8").splitlines()]
    assert [event["kind"] for event in events if event["kind"].startswith("lidar")] == [
        "lidar_started", "lidar_stopped"]


# --------------------------------------------------------------------------- runner
class StubImu:
    port, baud = "stub", 460800

    def __init__(self, **overrides):
        self.state = "live"
        self.payload = {"state": "live", "port": self.port, "baud": self.baud, "segment": 0,
                        "frames": 1200, "bytes": 80000, "hz": 199.5, "last_frame_age_s": 0.01,
                        "checksum_errors": 0, "payload_errors": 0, "sequence_breaks": 0,
                        "reconnects": 0, "queue_overflow": 0, "discarded_bytes": 0,
                        "first_tid": 1, "last_tid": 1200, "started_unix": 100.0, "ended_unix": 106.0}
        self.payload.update(overrides)

    def start(self):
        return self

    def stop(self):
        return self.status()

    def status(self):
        return dict(self.payload)


class StubLidar:
    iface, port = "stub0", 2368

    def __init__(self, **overrides):
        self.payload = {"state": "live", "iface": self.iface, "port": self.port, "pcap": "raw/lidar.pcap",
                        "pcap_bytes": 8_000_000, "started_unix": 100.2, "ended_unix": 106.0,
                        "captured": 6000, "received": 6000, "dropped": 0}
        self.payload.update(overrides)

    def start(self):
        return self

    def stop(self):
        return self.status()

    def status(self):
        return dict(self.payload)


def make_runner(tmp_path, imu_overrides=None, lidar_overrides=None, begin=100.0, end=106.0):
    session = CaptureSession(tmp_path / "sessions", session_id="runner")
    runner = CaptureRunner(session, imu=None, lidar=None, clock=lambda: end, wall=lambda: end)
    runner.imu = StubImu(**(imu_overrides or {}))
    runner.lidar = StubLidar(**(lidar_overrides or {}))
    runner.started_unix = begin
    return runner


def test_runner_ok_when_all_checks_pass(tmp_path):
    manifest = make_runner(tmp_path).stop("duration")
    assert manifest["status"] == "ok"
    quality = json.loads((tmp_path / "sessions" / "runner" / "quality.json").read_text("utf-8"))
    assert quality["time_level"] == "host_receive_only"
    assert quality["overlap_s"] == pytest.approx(5.8, abs=0.01)
    assert all(check["ok"] for check in quality["checks"])


def test_runner_marks_device_time_field_when_present(tmp_path):
    manifest = make_runner(tmp_path, {"sample_time_frames": 600}).stop("duration")
    quality = json.loads((tmp_path / "sessions" / "runner" / "quality.json").read_text("utf-8"))
    assert quality["time_level"] == "device_field_present_unverified"
    assert quality["imu"]["sample_time_frames"] == 600
    assert manifest["devices"]["imu"]["sample_time_field"] is True
    assert manifest["devices"]["imu"]["time_level"] == "device_field_present_unverified"
    assert any("0x51" in limit for limit in quality["measurement_limits"])


def test_runner_degraded_on_checksum_and_drops(tmp_path):
    manifest = make_runner(tmp_path, {"checksum_errors": 3}, {"dropped": 5}).stop("duration")
    assert manifest["status"] == "degraded"
    quality = json.loads((tmp_path / "sessions" / "runner" / "quality.json").read_text("utf-8"))
    failed = {check["name"] for check in quality["checks"] if not check["ok"]}
    assert failed == {"imu_checksum", "lidar_drops"}


def test_runner_failed_without_imu_frames(tmp_path):
    manifest = make_runner(tmp_path, {"frames": 0, "hz": 0.0}).stop("duration")
    assert manifest["status"] == "failed"


def test_runner_failed_on_imu_worker_error(tmp_path):
    manifest = make_runner(tmp_path, {"worker_error": "imu-writer: disk full"}).stop("duration")
    assert manifest["status"] == "failed"


def test_runner_marks_reconnect_and_payload_errors_degraded(tmp_path):
    manifest = make_runner(tmp_path, {"reconnects": 1, "payload_errors": 2}).stop("duration")
    assert manifest["status"] == "degraded"
    quality = json.loads((tmp_path / "sessions" / "runner" / "quality.json").read_text("utf-8"))
    assert {c["name"] for c in quality["checks"] if not c["ok"]} == {"imu_reconnect", "imu_payload"}


def test_imu_worker_exception_is_reported(tmp_path):
    session = CaptureSession(tmp_path, session_id="worker")
    recorder = ImuRecorder(session, "fake")

    def broken():
        raise OSError("disk full")

    recorder._run_worker(broken)
    assert "disk full" in recorder.status()["worker_error"]
    assert recorder._stop.is_set()


def test_raw_write_failure_stops_real_runner_and_marks_session_failed(tmp_path, monkeypatch):
    session = CaptureSession(tmp_path, session_id="disk-failure", min_free_bytes=0)
    original_open = session.open

    class FailedRaw:
        def write(self, _data):
            raise OSError("synthetic disk full")

        def flush(self):
            pass

        def close(self):
            pass

    def open_with_fault(relative, *args, **kwargs):
        return FailedRaw() if relative == "raw/imu.bin" else original_open(relative, *args, **kwargs)

    monkeypatch.setattr(session, "open", open_with_fault)
    runner = CaptureRunner(session, imu={"port": "fake", "serial_factory": lambda _p, _b: FakeSerial([build_frame(1)]),
                                         "flush_interval": 0.01}, poll_interval=0.02)
    runner.start()
    manifest = runner.run(2)
    quality = json.loads((session.path / "quality.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert quality["stop_reason"] == "imu_worker_failed"
    assert "synthetic disk full" in quality["imu"]["worker_error"]


def test_raw_flush_failure_marks_session_failed(tmp_path, monkeypatch):
    session = CaptureSession(tmp_path, session_id="flush-failure", min_free_bytes=0)
    original_open = session.open
    wrappers = []

    class FailingFlush:
        def __init__(self, handle):
            self.handle, self.fail = handle, False

        def write(self, data):
            return self.handle.write(data)

        def flush(self):
            if self.fail:
                raise OSError("synthetic flush failure")
            self.handle.flush()

        def close(self):
            self.handle.close()

    def open_with_fault(relative, *args, **kwargs):
        handle = original_open(relative, *args, **kwargs)
        if relative == "raw/imu.bin":
            handle = FailingFlush(handle)
            wrappers.append(handle)
        return handle

    monkeypatch.setattr(session, "open", open_with_fault)
    runner = CaptureRunner(session, imu={"port": "fake", "serial_factory": lambda _p, _b: FakeSerial([build_frame(1)]),
                                         "flush_interval": 0.01}, poll_interval=0.02)
    runner.start()
    deadline = time.monotonic() + 3
    while runner.imu.frames < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.imu.frames == 1
    wrappers[0].fail = True
    manifest = runner.stop()
    quality = json.loads((session.path / "quality.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert "synthetic flush failure" in quality["imu"]["worker_error"]


def test_runner_rejects_empty_pcap_and_unknown_drop_count(tmp_path):
    manifest = make_runner(tmp_path, lidar_overrides={"pcap_bytes": 24, "captured": 0, "dropped": None}).stop("duration")
    assert manifest["status"] == "failed"
    quality = json.loads((tmp_path / "sessions" / "runner" / "quality.json").read_text("utf-8"))
    assert {c["name"] for c in quality["checks"] if not c["ok"]} >= {"lidar_bytes", "lidar_drops"}


def test_runner_marks_disk_stop_degraded(tmp_path):
    assert make_runner(tmp_path).stop("disk_low")["status"] == "degraded"


def test_runner_marks_early_stop_degraded(tmp_path):
    assert make_runner(tmp_path).stop("user")["status"] == "degraded"


def test_runner_marks_lidar_process_failure(tmp_path):
    assert make_runner(tmp_path).stop("lidar_process_failed")["status"] == "failed"


def test_capture_root_prevents_parallel_recorders_and_releases_lock(tmp_path):
    first = CaptureRunner(CaptureSession(tmp_path, session_id="first"))
    second = CaptureRunner(CaptureSession(tmp_path, session_id="second"))
    first.start()
    try:
        with pytest.raises(RuntimeError, match="已有采集进程"):
            second.start()
    finally:
        first.stop()
    second.start()
    second.stop()


def test_capture_lock_excludes_other_process(tmp_path):
    lock = CaptureLock(tmp_path)
    code = ("from pathlib import Path; from na70_core.capture.lock import CaptureLock; "
            "lock = CaptureLock(Path(__import__('sys').argv[1])); lock.acquire(); lock.release()")
    lock.acquire()
    try:
        blocked = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True)
        assert blocked.returncode != 0
        assert b"RuntimeError" in blocked.stderr
    finally:
        lock.release()
    assert subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True).returncode == 0


def test_session_audit_detects_truncation(tmp_path):
    spec = importlib.util.spec_from_file_location("audit_na70_session", Path(__file__).resolve().parents[2] / "tools/audit_na70_session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = CaptureSession(tmp_path, session_id="audit")
    session.file("raw/imu.bin").write_bytes(b"abcdef")
    session.file("raw/imu_chunks.jsonl").write_text('{"offset":0,"bytes":6,"t_mono":1}\n', encoding="utf-8")
    session.file("raw/lidar.pcap").write_bytes(b"pcap")
    session.file("imu_frames.jsonl").write_text('{"tid":1,"segment":0,"t_mono":1}\n', encoding="utf-8")
    session.file("imu_host_receive.csv").write_text('host_monotonic_s\n1\n', encoding="utf-8")
    session.finalize(status="ok", quality={"status": "ok", "imu": {"frames": 1}})
    assert module.audit(session.path)["status"] == "ok"
    session.file("raw/imu.bin").write_bytes(b"abc")
    result = module.audit(session.path)
    assert result["status"] == "failed"
    assert any("raw/imu.bin" in error for error in result["errors"])
    assert any("覆盖" in error for error in result["errors"])
    session.file("raw/imu_chunks.jsonl").write_text(
        '{"offset":0,"bytes":2,"t_mono":2}\n{"offset":2,"bytes":1,"t_mono":1}\n', encoding="utf-8")
    assert any("倒退" in error for error in module.audit(session.path)["errors"])


def test_raw_imu_audit_matches_fragmented_frames_and_detects_changed_value(tmp_path):
    spec = importlib.util.spec_from_file_location("audit_na70_imu", Path(__file__).resolve().parents[2] / "tools/audit_na70_imu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = CaptureSession(tmp_path, session_id="raw-audit")
    stream = build_frame(10) + build_frame(11)
    recorder = ImuRecorder(session, "fake", serial_factory=lambda _p, _b: FakeSerial([stream[:20], stream[20:]]), flush_interval=0.01)
    recorder.start()
    deadline = time.monotonic() + 3
    while recorder.frames < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    recorder.stop()
    result = module.audit(session.path)
    assert result["status"] == "ok" and result["matched_frames"] == 2
    frame_file = session.path / "imu_frames.jsonl"
    frames = [json.loads(line) for line in frame_file.read_text(encoding="utf-8").splitlines()]
    frames[0]["accel"][0] += 1
    frame_file.write_text("".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8")
    assert module.audit(session.path)["mismatches"] == 1
    session.close()


def test_sudo_interrupt_targets_only_capture_child(tmp_path, monkeypatch):
    from na70_core.capture import lidar as lidar_module

    calls = []
    monkeypatch.setattr(lidar_module.subprocess, "run", lambda command, **kwargs: calls.append(command))
    capture = LidarCapture(CaptureSession(tmp_path, session_id="scope"), "stub", use_sudo=True, euid=1000)
    capture._process = type("Process", (), {"pid": 12345})()
    capture._interrupt_child()
    assert calls == [["sudo", "-n", "pkill", "-INT", "-P", "12345", "-x", "tcpdump"]]


def test_runner_flags_low_overlap(tmp_path):
    manifest = make_runner(tmp_path, {"started_unix": 105.0}, {"ended_unix": 101.0}).stop("duration")
    assert manifest["status"] == "degraded"
    quality = json.loads((tmp_path / "sessions" / "runner" / "quality.json").read_text("utf-8"))
    overlap = next(check for check in quality["checks"] if check["name"] == "overlap")
    assert not overlap["ok"]
