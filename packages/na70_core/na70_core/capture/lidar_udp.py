"""雷达原始 PCAP 捕获（本机 UDP socket，Windows/Linux 纯 Python）。

为什么不用 tcpdump：``lidar.py`` 依赖 tcpdump 子进程，仅 Linux 可用。本机
（Windows）直采时雷达以单播 UDP 发到电脑 IP:2368，普通 UDP socket 即可收包，
无需 npcap。采集端仍只存原始字节，不解析点云：每个 datagram 补上合成的
Ethernet/IPv4/UDP 帧头写入经典 PCAP 2.4（network=1），``web/backend/decoder.py``
与其下游工具直接可读。

局限（写入日志与质量报告）：

* Windows 拿不到内核丢包计数，``dropped`` 恒为 0，不代表真的零丢包。
* 合成帧头的 MAC 置零、IP 头校验和为真实计算，但源 MAC 无法从 UDP socket 得知。
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections import deque
from pathlib import Path

PCAP = "raw/lidar.pcap"
LOG = "raw/lidar-udp.log"

_ETH_HEADER = b"\x00" * 12 + b"\x08\x00"  # MAC 置零 + IPv4


class LidarError(RuntimeError):
    pass


def _ip_checksum(header):
    total = 0
    for offset in range(0, len(header), 2):
        total += (header[offset] << 8) | header[offset + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def local_ip_toward(peer):
    """取本机到 peer 的路由源地址（UDP connect 不发包）。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer, 9))
        return probe.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        probe.close()


class UdpLidarCapture:
    """与 ``LidarCapture`` 同一鸭子接口的 UDP 抓包器（读线程 + PCAP 落盘）。"""

    capture_method = "udp socket (synthesized Ethernet/IP/UDP headers, raw payload only)"

    def __init__(self, session, *, port=2368, bind_host="",
                 rate_window=2.0, clock=time.monotonic, wall=time.time,
                 socket_factory=None):
        self.session = session
        self.port = int(port)
        self.bind_host = str(bind_host)
        self.rate_window = float(rate_window)
        self.clock = clock
        self.wall = wall
        self._socket_factory = socket_factory
        self.pcap = session.file(PCAP)
        self.log_path = session.file(LOG)
        self.started_unix = None
        self.started_mono = None
        self.ended_unix = None
        self.ended_mono = None
        self.stats = {"captured": 0, "received": 0, "dropped": 0}
        self.state = "idle"
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._sock = None
        self._pcap_file = None
        self._log = None
        self._arrivals = deque()
        self._worker_error = ""
        self._local_ip = None

    # ------------------------------------------------------------- internals
    def _log_line(self, message):
        if self._log is not None:
            self._log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
            self._log.flush()

    def _make_socket(self):
        if self._socket_factory is not None:
            return self._socket_factory()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind_host, self.port))
        sock.settimeout(0.2)
        return sock

    def _frame(self, payload, src_ip, dst_ip, src_port):
        udp_length = 8 + len(payload)
        udp = struct.pack(">HHHH", src_port, self.port, udp_length, 0) + payload
        total = 20 + udp_length
        src = socket.inet_aton(src_ip)
        dst = socket.inet_aton(dst_ip)
        base = struct.pack(">BBHHHBBH", 0x45, 0, total, 0, 0x4000, 64, 17, 0) + src + dst
        ip = struct.pack(">BBHHHBBH", 0x45, 0, total, 0, 0x4000, 64, 17,
                         _ip_checksum(base)) + src + dst
        return _ETH_HEADER + ip + udp

    def _write_record(self, frame, unix):
        seconds = int(unix)
        microseconds = round((unix - seconds) * 1_000_000)
        if microseconds >= 1_000_000:
            seconds += 1
            microseconds -= 1_000_000
        self._pcap_file.write(struct.pack("<IIII", seconds, microseconds,
                                          len(frame), len(frame)))
        self._pcap_file.write(frame)

    def _record_worker_error(self, exc):
        with self._lock:
            if not self._worker_error:
                self._worker_error = f"{threading.current_thread().name}: {type(exc).__name__}: {exc}"
            self.state = "failed"
            self.error = self._worker_error
        self._stop.set()
        try:
            self.session.event("lidar_worker_failed", error=self._worker_error)
        except Exception:
            pass

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                payload, sender = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self._record_worker_error(exc)
                break
            unix = self.wall()
            with self._lock:
                self.stats["received"] += 1
                now = self.clock()
                self._arrivals.append(now)
                cutoff = now - self.rate_window
                while self._arrivals and self._arrivals[0] < cutoff:
                    self._arrivals.popleft()
            src_ip, src_port = sender[0], sender[1]
            if self._local_ip is None:
                self._local_ip = local_ip_toward(src_ip)
                self._log_line(f"local_ip={self._local_ip} sender={src_ip}:{src_port}")
            try:
                self._write_record(self._frame(payload, src_ip, self._local_ip, src_port), unix)
            except OSError as exc:
                self._record_worker_error(exc)
                break
            with self._lock:
                self.stats["captured"] += 1
        with self._lock:
            if self.state != "failed":
                self.state = "stopped"

    # ------------------------------------------------------------------ start
    def start(self):
        if self._thread is not None:
            raise LidarError("雷达捕获已启动")
        self.session.ensure_disk()
        if self.pcap.exists() and self.pcap.stat().st_size:
            raise LidarError(f"PCAP 已存在且非空，拒绝覆盖：{self.pcap}")
        self._log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        try:
            self._sock = self._make_socket()
        except OSError as exc:
            self._log.close()
            self._log = None
            raise LidarError(f"无法绑定 UDP 端口 {self.port}（被占用或防火墙拦截）：{exc}") from exc
        self._pcap_file = open(self.pcap, "wb")
        self._pcap_file.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        self._pcap_file.flush()
        self.started_unix = self.wall()
        self.started_mono = self.clock()
        self._stop.clear()
        self.state = "live"
        self._thread = threading.Thread(target=self._guarded_read_loop, name="lidar-udp", daemon=True)
        self._thread.start()
        self.session.event("lidar_started", bind_host=self.bind_host or "0.0.0.0",
                           port=self.port, capture="udp_socket")
        return self

    def _guarded_read_loop(self):
        try:
            self._read_loop()
        except Exception as exc:  # noqa: BLE001 - 读线程异常必须进入质量报告
            self._record_worker_error(exc)

    # ------------------------------------------------------------------- stop
    def stop(self, timeout=10.0):
        if self._thread is None:
            return self.status()
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise LidarError("UDP 读取线程未停止，保留文件句柄")
        self._thread = None
        self.ended_unix = self.wall()
        self.ended_mono = self.clock()
        for handle in (self._pcap_file, self._log):
            if handle is not None:
                try:
                    handle.flush()
                except (OSError, ValueError) as exc:
                    self._record_worker_error(exc)
                try:
                    handle.close()
                except (OSError, ValueError) as exc:
                    self._record_worker_error(exc)
        self._pcap_file = self._log = None
        with self._lock:
            if self.state != "failed":
                self.state = "stopped"
        self.session.event("lidar_stopped",
                           pcap_bytes=self.pcap.stat().st_size if self.pcap.exists() else 0,
                           **self.stats,
                           note="dropped 由 tcpdump 才可得，UDP 采集恒 0 不代表零丢包")
        return self.status()

    # ----------------------------------------------------------------- status
    def status(self):
        size = self.pcap.stat().st_size if self.pcap.exists() else 0
        with self._lock:
            captured = self.stats["captured"]
            arrivals = tuple(self._arrivals)
            state, error = self.state, self.error
        now = self.clock()
        recent = len(arrivals)
        if state == "live":
            span = min(self.rate_window, max(now - (self.started_mono or now), 1e-6))
            rate = recent / span if recent else 0.0
        elif self.started_mono and self.ended_mono and self.ended_mono > self.started_mono:
            # 停机后“最近 2 秒”必然为空，此时报告整段平均频率，而不是 0 Hz。
            rate = captured / (self.ended_mono - self.started_mono)
        else:
            rate = 0.0
        return {
            "state": state,
            "error": error,
            "worker_error": self._worker_error,
            "bind_host": self.bind_host or "0.0.0.0",
            "port": self.port,
            "local_ip": self._local_ip,
            "pcap": str(self.pcap),
            "pcap_bytes": size,
            "started_unix": self.started_unix,
            "ended_unix": self.ended_unix,
            "packets_per_sec": round(rate, 1),
            **self.stats,
            "capture_method": self.capture_method,
        }
