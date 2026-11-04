"""把 IMU 与雷达两个采集端组成一次会话，并输出质量报告。

质量判定只描述“本次数据能不能用于下一步”，不代替标定结论：
采集正常 ≠ 时间已同步 ≠ 外参已可解。
"""

from __future__ import annotations

import time

from .imu import ImuRecorder
from .lidar import LidarCapture
from .lock import CaptureLock
from .session import CaptureSession

TIME_LEVEL_HOST_ONLY = "host_receive_only"
TIME_LEVEL_DEVICE_FIELD = "device_field_present_unverified"
MIN_OVERLAP_FRACTION = 0.9
IMU_RATE_BOUNDS = (150.0, 260.0)


class CaptureRunner:
    def __init__(self, session: CaptureSession, *, imu=None, lidar=None,
                 clock=time.monotonic, wall=time.time, poll_interval=1.0,
                 disk_free=None):
        self.session = session
        self.clock = clock
        self.wall = wall
        self.poll_interval = float(poll_interval)
        self._disk_free = disk_free
        # 允许传已构造好的实例（如 Windows 的 UdpLidarCapture），dict 才在
        # 这里构造默认实现。
        self.imu = ImuRecorder(session, **imu) if isinstance(imu, dict) else imu
        self.lidar = LidarCapture(session, **lidar) if isinstance(lidar, dict) else lidar
        self.started_unix = None
        self.ended_unix = None
        self.stop_reason = None
        self._warnings = []
        self._capture_lock = CaptureLock(session.root)

    # ------------------------------------------------------------------ control
    def start(self):
        self._capture_lock.acquire()
        try:
            self.session.ensure_disk()
            self.started_unix = self.wall()
            self.session.event("capture_started", imu=bool(self.imu), lidar=bool(self.lidar))
            if self.imu is not None:
                self.imu.start()
            if self.lidar is not None:
                self.lidar.start()
        except Exception:
            self._safe_stop()
            raise
        return self

    def run(self, seconds, on_tick=None, tick_interval=2.0, should_stop=None):
        deadline = self.clock() + float(seconds)
        next_tick = self.clock()
        while self.clock() < deadline:
            if should_stop is not None and should_stop():
                self.stop_reason = "user"
                break
            remaining = deadline - self.clock()
            time.sleep(min(self.poll_interval, max(remaining, 0.05)))
            free = self.session.free_bytes()
            if free < self.session.min_free_bytes:
                self.stop_reason = "disk_low"
                self.session.event("disk_low", free_bytes=free)
                break
            if self.imu is not None and self.imu.status().get("worker_error"):
                self.stop_reason = "imu_worker_failed"
                break
            if self.lidar is not None and self.lidar.status().get("state") == "failed":
                self.stop_reason = "lidar_process_failed"
                break
            if self.clock() >= next_tick:
                next_tick = self.clock() + max(tick_interval, self.poll_interval)
                if on_tick is not None:
                    on_tick(self.status(), max(deadline - self.clock(), 0.0))
        return self.stop(self.stop_reason or "duration")

    def stop(self, reason="user"):
        self.stop_reason = self.stop_reason or reason
        self.session.event("capture_stopping", reason=self.stop_reason)
        self._safe_stop()
        self.ended_unix = self.wall()
        return self.finalize()

    def _safe_stop(self):
        failed = False
        for recorder in (self.imu, self.lidar):
            if recorder is None:
                continue
            try:
                recorder.stop()
            except Exception as exc:  # noqa: BLE001 - 停止失败必须记录而不是静默
                failed = True
                self.session.event("stop_failed", device=type(recorder).__name__, error=str(exc))
                self._warnings.append(f"{type(recorder).__name__} 停止异常：{exc}")
        if not failed:
            self._capture_lock.release()

    # ---------------------------------------------------------------- reporting
    def status(self):
        started = getattr(self.imu, "started_mono", None) if self.imu is not None else None
        payload = {"elapsed_s": round(self.clock() - started, 3) if started else 0.0,
                   "stop_reason": self.stop_reason}
        if self.imu is not None:
            payload["imu"] = self.imu.status()
        if self.lidar is not None:
            payload["lidar"] = self.lidar.status()
        return payload

    @staticmethod
    def _window(status):
        if not status or status.get("started_unix") is None:
            return None
        return status["started_unix"], status.get("ended_unix")

    def _overlap(self, imu_status=None, lidar_status=None):
        """两路采集进程运行时间窗的交集，不证明有效数据连续重叠。"""
        windows = [window for window in (self._window(imu_status), self._window(lidar_status))
                   if window is not None]
        if len(windows) < 2:
            return 0.0
        ends = [end if end is not None else self.wall() for _, end in windows]
        return max(0.0, min(ends) - max(start for start, _ in windows))

    def finalize(self):
        imu_status = self.imu.status() if self.imu is not None else None
        lidar_status = self.lidar.status() if self.lidar is not None else None
        overlap = self._overlap(imu_status, lidar_status)
        duration = (self.ended_unix or self.wall()) - (self.started_unix or self.wall())
        checks = []
        if imu_status is not None:
            hz = imu_status["hz"]
            checks.append({"name": "imu_frames", "ok": imu_status["frames"] > 0,
                           "detail": f"{imu_status['frames']} 帧"})
            checks.append({"name": "imu_rate", "ok": IMU_RATE_BOUNDS[0] <= hz <= IMU_RATE_BOUNDS[1],
                           "detail": f"{hz} Hz（期望 150–260）"})
            checks.append({"name": "imu_checksum", "ok": imu_status["checksum_errors"] == 0,
                           "detail": f"{imu_status['checksum_errors']} 个校验错误"})
            checks.append({"name": "imu_payload", "ok": imu_status.get("payload_errors", 0) == 0,
                           "detail": f"{imu_status.get('payload_errors', 0)} 个载荷错误"})
            checks.append({"name": "imu_reconnect", "ok": imu_status.get("reconnects", 0) == 0,
                           "detail": f"{imu_status.get('reconnects', 0)} 次打开或读取失败"})
            checks.append({"name": "imu_sequence", "ok": imu_status["sequence_breaks"] == 0,
                           "detail": f"{imu_status['sequence_breaks']} 次 tid 断号"})
            checks.append({"name": "imu_overflow", "ok": imu_status["queue_overflow"] == 0,
                           "detail": f"{imu_status['queue_overflow']} 帧写队列溢出"})
            checks.append({"name": "imu_worker", "ok": not imu_status.get("worker_error"),
                           "detail": imu_status.get("worker_error") or "采集线程正常"})
        if lidar_status is not None:
            # A classic PCAP global header alone contains no packets.
            checks.append({"name": "lidar_bytes", "ok": lidar_status["pcap_bytes"] > 24 and lidar_status["captured"] != 0,
                           "detail": f"{lidar_status['pcap_bytes']} 字节"})
            drops = lidar_status["dropped"]
            checks.append({"name": "lidar_drops", "ok": drops == 0,
                           "detail": f"内核丢弃 {drops}"})
        if self.stop_reason == "disk_low":
            checks.append({"name": "disk_space", "ok": False, "detail": "磁盘空间不足，提前停止"})
        if self.stop_reason == "user":
            checks.append({"name": "early_stop", "ok": False, "detail": "录制被提前停止，需检查时长"})
        if self.stop_reason == "lidar_process_failed":
            checks.append({"name": "lidar_process", "ok": False, "detail": "抓包进程提前退出"})
        if self._warnings:
            checks.append({"name": "recorder_stop", "ok": False, "detail": "; ".join(self._warnings)})
        if imu_status is not None and lidar_status is not None:
            # 用“较短流”的长度做分母：停止 tcpdump 需要几秒，会话墙钟长度包含
            # 这段收尾时间，用它当分母会把本来完整的重叠判成不合格。
            windows = [window for window in (self._window(imu_status), self._window(lidar_status))
                       if window is not None]
            spans = [(end if end is not None else self.wall()) - start for start, end in windows]
            denominator = min(spans) if spans else 0.0
            fraction = overlap / denominator if denominator > 0 else 0.0
            checks.append({"name": "overlap", "ok": fraction >= MIN_OVERLAP_FRACTION,
                           "detail": f"采集进程窗口 {overlap:.2f} s，覆盖较短窗口 {fraction * 100:.0f}%"})
        failed = [check for check in checks if not check["ok"]]
        essential = {"imu_frames", "lidar_bytes", "imu_worker", "lidar_process"}
        if any(check["name"] in essential for check in failed):
            status = "failed"
        elif failed:
            status = "degraded"
        else:
            status = "ok"
        sample_time_present = bool(imu_status and imu_status.get("sample_time_frames"))
        time_level = TIME_LEVEL_DEVICE_FIELD if sample_time_present else TIME_LEVEL_HOST_ONLY
        limits = [
            "IMU 有 0x51 设备时间字段，但语义、漂移与雷达同步未验证"
            if sample_time_present else "IMU 无设备采样时间戳：主机到达时间不是采样时间",
            "雷达包内时间未核实，PCAP 时间为主机抓包时间",
            "两路同一主机只提供关联，不构成硬件同步",
            "overlap_s 仅表示采集进程运行窗口，不证明两路数据连续有效",
        ]
        quality = {
            "schema": "na70-capture-quality-v1",
            "status": status,
            "stop_reason": self.stop_reason,
            "duration_s": round(duration, 3),
            "overlap_s": round(overlap, 3),
            "time_level": time_level if imu_status else None,
            "measurement_limits": limits,
            "checks": checks,
            "imu": imu_status,
            "lidar": lidar_status,
            "warnings": self._warnings,
        }
        devices = {
            "imu": {
                "protocol": "YESENSE 0x59/0x53, 67 B frames",
                "port": self.imu.port if self.imu else None,
                "baud": self.imu.baud if self.imu else None,
                "units": {"accel": "m/s^2", "gyro": "deg/s", "euler": "deg"},
                "unit_evidence": {"accel": "verified", "gyro": "inferred", "euler": "inferred"},
                "sample_time_field": sample_time_present,
                "sample_time_frames": imu_status.get("sample_time_frames") if imu_status else None,
                "time_level": time_level,
            } if self.imu is not None else None,
            "lidar": {
                "iface": self.lidar.iface if self.lidar else None,
                "udp_port": self.lidar.port if self.lidar else None,
                "payload_bytes_expected": 1410,
                "capture": getattr(self.lidar, "capture_method", "tcpdump -w (raw bytes only)"),
            } if self.lidar is not None else None,
        }
        windows = {
            "session": {"start_unix": self.started_unix, "end_unix": self.ended_unix},
            "imu": {"start_unix": imu_status["started_unix"], "end_unix": imu_status["ended_unix"]} if imu_status else None,
            "lidar": {"start_unix": lidar_status["started_unix"], "end_unix": lidar_status["ended_unix"]} if lidar_status else None,
            "overlap_s": round(overlap, 3),
        }
        notes = list(self._warnings)
        if imu_status is not None and imu_status["frames"] and imu_status["hz"]:
            notes.append(f"IMU 实测 {imu_status['hz']} Hz")
        return self.session.finalize(status=status, quality=quality, devices=devices,
                                     windows=windows, notes=notes)
