"""IMU 原始字节与解码帧的持续落盘（单进程独占串口）。

纪律：

* **原始字节优先**：接收块先按到达顺序写入 ``raw/imu.bin``，再解析；解析失败
  或写帧队列溢出都不影响原始证据。
* 每个接收块记录偏移、长度、单调/墙上时间与 segment，便于事后对账。
* 断流/重连开启新 segment，并清空半帧缓冲（残留字节计入 ``discarded_bytes``）。
* 没有设备采样时间戳时**不伪造** ``device_time_s``：只导出
  ``imu_host_receive.csv``，质量报告写明 ``host_receive_only``。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque

from .yesense import FrameParser, next_tid, tid_follows

CSV_HEADER = ("host_monotonic_s", "host_unix_s", "tid", "segment",
              "ax_m_s2", "ay_m_s2", "az_m_s2",
              "gx_deg_s", "gy_deg_s", "gz_deg_s",
              "pitch_deg", "roll_deg", "yaw_deg",
              "qw", "qx", "qy", "qz", "sample_us")


def _fmt(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


class ImuRecorder:
    def __init__(self, session, port, *, baud=460800, queue_max=20000,
                 flush_interval=1.0, reconnect_delay=1.0, read_timeout=0.05,
                 clock=time.monotonic, wall=time.time, serial_factory=None,
                 max_read_bytes=8192, rate_window=2.0):
        self.session = session
        self.port = str(port)
        self.baud = int(baud)
        self.queue_max = int(queue_max)
        self.flush_interval = float(flush_interval)
        self.reconnect_delay = float(reconnect_delay)
        self.read_timeout = float(read_timeout)
        self.clock = clock
        self.wall = wall
        self.max_read_bytes = int(max_read_bytes)
        self.rate_window = float(rate_window)
        self._serial_factory = serial_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._queue = queue.Queue(maxsize=self.queue_max)
        self._reader = None
        self._writer = None
        self._raw = None
        self._chunks = None
        self._frames_file = None
        self._csv = None
        self._state = "idle"
        self._error = ""
        self._worker_error = ""
        self._arrivals = deque()
        self._reset_counters()

    # ------------------------------------------------------------- internals
    def _reset_counters(self):
        self.frames = 0
        self.bytes = 0
        self.raw_offset = 0
        self.segment = 0
        self.checksum_errors = 0
        self.payload_errors = 0
        self.sequence_breaks = 0
        self.reconnects = 0
        self.queue_overflow = 0
        self.discarded_bytes = 0
        self.sample_time_frames = 0
        self.first_tid = None
        self.last_tid = None
        self.started_unix = None
        self.started_mono = None
        self.ended_unix = None

    def _factory(self):
        if self._serial_factory is not None:
            return self._serial_factory(self.port, self.baud)
        import serial  # 延迟导入：只有真正连设备时才需要 pyserial
        return serial.Serial(self.port, self.baud, timeout=self.read_timeout,
                             write_timeout=0.2)

    def _set_state(self, state, error=""):
        with self._lock:
            self._state, self._error = state, error

    # ------------------------------------------------------------------ start
    def start(self):
        if self._reader is not None:
            raise RuntimeError("IMU 录制已启动")
        self.session.ensure_disk()
        self._raw = self.session.open("raw/imu.bin", "ab")
        self._chunks = self.session.open("raw/imu_chunks.jsonl", "a", text=True)
        self._frames_file = self.session.open("imu_frames.jsonl", "a", text=True)
        self._csv = self.session.open("imu_host_receive.csv", "a", text=True)
        self._csv.write(",".join(CSV_HEADER) + "\n")
        self._csv.flush()
        self.started_unix = self.wall()
        self.started_mono = self.clock()
        self._stop.clear()
        self._state = "waiting"
        self._writer = threading.Thread(target=self._run_worker, args=(self._write_loop,), name="imu-writer", daemon=True)
        self._reader = threading.Thread(target=self._run_worker, args=(self._read_loop,), name="imu-reader", daemon=True)
        self._writer.start()
        self._reader.start()
        self.session.event("imu_started", port=self.port, baud=self.baud, segment=self.segment)
        return self

    # ------------------------------------------------------------------- stop
    def stop(self, timeout=10.0):
        if self._reader is None:
            return self.status()
        self._stop.set()
        self._reader.join(timeout=timeout)
        if self._reader.is_alive():
            raise RuntimeError("IMU 读取线程未停止，保留文件句柄")
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:
            raise RuntimeError("IMU 写入队列未排空")
        if self._writer is not None:
            self._writer.join(timeout=timeout)
            if self._writer.is_alive():
                raise RuntimeError("IMU 写入线程未停止，保留文件句柄")
        self.ended_unix = self.wall()
        for handle in (self._raw, self._chunks, self._frames_file, self._csv):
            if handle is not None:
                try:
                    handle.flush()
                except (OSError, ValueError) as exc:
                    self._record_worker_error(exc)
                try:
                    handle.close()
                except (OSError, ValueError) as exc:
                    self._record_worker_error(exc)
        self._raw = self._chunks = self._frames_file = self._csv = None
        self._reader = self._writer = None
        self._set_state("stopped")
        self.session.event("imu_stopped", frames=self.frames, bytes=self.bytes,
                           checksum_errors=self.checksum_errors,
                           payload_errors=self.payload_errors,
                           sequence_breaks=self.sequence_breaks,
                           reconnects=self.reconnects, queue_overflow=self.queue_overflow)
        return self.status()

    # ------------------------------------------------------------------ loops
    def _run_worker(self, worker):
        try:
            worker()
        except Exception as exc:  # noqa: BLE001 - 工作线程异常必须进入质量报告
            self._record_worker_error(exc)

    def _record_worker_error(self, exc):
        with self._lock:
            if not self._worker_error:
                self._worker_error = f"{threading.current_thread().name}: {type(exc).__name__}: {exc}"
            self._state = "failed"
            self._error = self._worker_error
        self._stop.set()
        try:
            self.session.event("imu_worker_failed", error=self._worker_error)
        except Exception:
            pass

    def _read_loop(self):
        parser = FrameParser()
        while not self._stop.is_set():
            try:
                device = self._factory()
            except Exception as exc:  # noqa: BLE001 - 串口打开失败要如实记录
                self.reconnects += 1
                self._set_state("disconnected", str(exc))
                self.session.event("imu_open_failed", error=str(exc), attempt=self.reconnects)
                if self._stop.wait(self.reconnect_delay):
                    break
                continue
            with device:
                if self.frames:
                    self.segment += 1  # 已有数据后重连 → 新 segment，避免混段
                self._set_state("live")
                self.session.event("imu_segment_open", segment=self.segment, port=self.port)
                while not self._stop.is_set():
                    try:
                        chunk = device.read(self.max_read_bytes)
                    except Exception as exc:  # noqa: BLE001
                        self.reconnects += 1
                        self._set_state("disconnected", str(exc))
                        self.session.event("imu_read_failed", error=str(exc), segment=self.segment)
                        break
                    if chunk:
                        self._on_chunk(parser, chunk)
                parser.reset()
                self.discarded_bytes += parser.discarded_bytes
                parser.discarded_bytes = 0
                if not self._stop.is_set() and self._state != "disconnected":
                    self._set_state("waiting")
                if not self._stop.is_set():
                    self._stop.wait(self.reconnect_delay)
        self._set_state("stopped")

    def _on_chunk(self, parser, chunk):
        mono = self.clock()
        unix = self.wall()
        offset = self.raw_offset
        self._raw.write(chunk)
        self.raw_offset += len(chunk)
        self.bytes += len(chunk)
        self._chunks.write(json.dumps({
            "segment": self.segment, "offset": offset, "bytes": len(chunk),
            "t_mono": round(mono, 6), "t_unix": round(unix, 6)}, ensure_ascii=False) + "\n")
        frames = parser.feed(chunk)
        self.checksum_errors = parser.bad_checksum
        self.payload_errors = parser.bad_payload
        if parser.discarded_bytes:
            self.discarded_bytes += parser.discarded_bytes
            parser.discarded_bytes = 0
        for frame in frames:
            if self.last_tid is not None and not tid_follows(self.last_tid, frame.tid):
                self.sequence_breaks += 1
                if self.sequence_breaks <= 20:
                    self.session.event("imu_sequence_break", expected=next_tid(self.last_tid),
                                       got=frame.tid, segment=self.segment)
            if self.first_tid is None:
                self.first_tid = frame.tid
            self.last_tid = frame.tid
            self.frames += 1
            if frame.sample_us is not None:
                self.sample_time_frames += 1
            with self._lock:
                self._arrivals.append(mono)
                cutoff = mono - self.rate_window
                while self._arrivals and self._arrivals[0] < cutoff:
                    self._arrivals.popleft()
            try:
                self._queue.put_nowait((mono, unix, self.segment, frame))
            except queue.Full:
                self.queue_overflow += 1
                if self.queue_overflow in (1, 100, 10000):
                    self.session.event("imu_queue_overflow", total=self.queue_overflow)

    def _write_loop(self):
        deadline = self.clock()
        while True:
            try:
                item = self._queue.get(timeout=self.flush_interval)
            except queue.Empty:
                self._flush()
                continue
            if item is None:
                self._flush()
                return
            mono, unix, segment, frame = item
            record = {"t_mono": round(mono, 6), "t_unix": round(unix, 6), "segment": segment,
                      "tid": frame.tid}
            for name in ("accel", "gyro", "euler", "quaternion"):
                value = getattr(frame, name)
                if value is not None:
                    record[name] = [round(v, 6) for v in value]
            if frame.sample_us is not None:
                record["sample_us"] = frame.sample_us
            self._frames_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            accel = frame.accel or (None, None, None)
            gyro = frame.gyro or (None, None, None)
            euler = frame.euler or (None, None, None)
            quat = frame.quaternion or (None,) * 4
            self._csv.write(",".join([
                f"{mono - self.started_mono:.6f}", f"{unix:.6f}", str(frame.tid), str(segment),
                _fmt(accel[0]), _fmt(accel[1]), _fmt(accel[2]),
                _fmt(gyro[0]), _fmt(gyro[1]), _fmt(gyro[2]),
                _fmt(euler[0]), _fmt(euler[1]), _fmt(euler[2]),
                _fmt(quat[0]), _fmt(quat[1]), _fmt(quat[2]), _fmt(quat[3]),
                _fmt(frame.sample_us)]) + "\n")
            now = self.clock()
            if now - deadline >= self.flush_interval:
                self._flush()
                deadline = now

    def _flush(self):
        for handle in (self._frames_file, self._csv, self._chunks, self._raw):
            try:
                if handle is not None:
                    handle.flush()
            except (OSError, ValueError):
                raise

    # ---------------------------------------------------------------- status
    def status(self):
        with self._lock:
            state, error = self._state, self._error
            arrivals = tuple(self._arrivals)
        now = self.clock()
        recent = [t for t in arrivals if t >= now - self.rate_window]
        span = min(self.rate_window, max(now - (self.started_mono or now), 1e-6))
        # 停机后“最近 2 秒”必然为空，此时报告整段平均频率，而不是 0 Hz。
        if state == "stopped" and self.started_unix and self.ended_unix and self.ended_unix > self.started_unix:
            hz = self.frames / (self.ended_unix - self.started_unix)
        else:
            hz = len(recent) / span if recent else 0.0
        age = round(now - arrivals[-1], 3) if arrivals and state != "stopped" else None
        return {
            "state": state,
            "error": error,
            "worker_error": self._worker_error,
            "port": self.port,
            "baud": self.baud,
            "segment": self.segment,
            "frames": self.frames,
            "bytes": self.bytes,
            "hz": round(hz, 1),
            "last_frame_age_s": age,
            "checksum_errors": self.checksum_errors,
            "payload_errors": self.payload_errors,
            "sequence_breaks": self.sequence_breaks,
            "reconnects": self.reconnects,
            "queue_overflow": self.queue_overflow,
            "discarded_bytes": self.discarded_bytes,
            "sample_time_frames": self.sample_time_frames,
            "first_tid": self.first_tid,
            "last_tid": self.last_tid,
            "started_unix": self.started_unix,
            "ended_unix": self.ended_unix,
        }
