"""雷达原始 PCAP 捕获（tcpdump 子进程，不解析点云）。

为什么用 tcpdump 而不是自带 socket 解析：解码器已固定在
``web/backend/decoder.py``，采集端只负责把原始字节按到达顺序写入 PCAP，
不复制第二套协议实现。丢包统计直接取 tcpdump 退出时的内核计数。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

PCAP = "raw/lidar.pcap"
LOG = "raw/lidar-tcpdump.log"

_STAT_PATTERNS = {
    "captured": re.compile(r"(\d+) packets captured"),
    "received": re.compile(r"(\d+) packets received by filter"),
    "dropped": re.compile(r"(\d+) packets dropped by kernel"),
}


class LidarError(RuntimeError):
    pass


class LidarCapture:
    def __init__(self, session, iface, *, port=2368, tcpdump="tcpdump", use_sudo=None,
                 run_as=None, snaplen=0, settle=0.8, stop_timeout=20.0,
                 popen=None, euid=None, clock=time.monotonic, wall=time.time):
        self.session = session
        self.iface = str(iface)
        self.port = int(port)
        self.tcpdump = tcpdump
        self.snaplen = int(snaplen)
        self.settle = float(settle)
        self.stop_timeout = float(stop_timeout)
        self.run_as = run_as
        self.clock = clock
        self.wall = wall
        self._popen = popen or subprocess.Popen
        self._process = None
        self._log = None
        self._euid = os.geteuid() if euid is None else euid
        self.use_sudo = (self._euid != 0) if use_sudo is None else bool(use_sudo)
        self.pcap = session.file(PCAP)
        self.log_path = session.file(LOG)
        self.started_unix = None
        self.ended_unix = None
        self.stats = {"captured": None, "received": None, "dropped": None}
        self.state = "idle"
        self.error = ""

    def command(self):
        command = [self.tcpdump, "-i", self.iface, "-nn", "-s", str(self.snaplen),
                   "-U", "-w", str(self.pcap), f"udp port {self.port}"]
        if self.run_as and self._euid == 0:
            command[1:1] = ["-Z", self.run_as]
        if self.use_sudo:
            command = ["sudo", "-n"] + command
        return command

    def start(self):
        if self._process is not None:
            raise LidarError("雷达捕获已启动")
        self.session.ensure_disk()
        if self.pcap.exists() and self.pcap.stat().st_size:
            raise LidarError(f"PCAP 已存在且非空，拒绝覆盖：{self.pcap}")
        self._log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        command = self.command()
        self.started_unix = self.wall()
        try:
            self._process = self._popen(command, stdout=self._log, stderr=self._log,
                                        stdin=subprocess.DEVNULL)
        except (OSError, FileNotFoundError) as exc:
            self._log.close()
            self._log = None
            raise LidarError(f"无法启动 tcpdump：{exc}") from exc
        deadline = self.clock() + max(self.settle, 0.2) + 5.0
        while self.clock() < deadline:
            if self._process.poll() is not None:
                tail = self._log_tail()
                self._log.close()
                self._log = None
                self._process = None
                raise LidarError(f"tcpdump 启动后立即退出：{tail}")
            if self.pcap.exists() and self.pcap.stat().st_size > 0:
                break
            time.sleep(0.05)
        self.state = "live"
        self.session.event("lidar_started", iface=self.iface, port=self.port,
                           command=" ".join(command))
        return self

    def _log_tail(self, lines=12):
        try:
            return "\n".join(self.log_path.read_text("utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    @staticmethod
    def _signal(target, sig):
        try:
            if hasattr(target, "send_signal"):
                target.send_signal(sig)
            else:
                os.kill(int(target), sig)
        except (ProcessLookupError, OSError, ValueError):
            pass

    @staticmethod
    def _children(process):
        """Linux /proc 下的直接子进程（同用户时可读，root 子进程通常不可读）。"""
        pid = getattr(process, "pid", None)
        if not pid:
            return []
        try:
            text = Path(f"/proc/{pid}/task/{pid}/children").read_text("ascii")
        except (OSError, ValueError):
            return []
        children = []
        for token in text.split():
            try:
                children.append(int(token))
            except ValueError:
                continue
        return children

    def _interrupt_child(self):
        """仅向本次 sudo 进程的 tcpdump 子进程发 SIGINT。"""
        if not self.use_sudo or self._euid == 0:
            return
        pid = getattr(self._process, "pid", None)
        if not pid:
            return
        try:
            subprocess.run(["sudo", "-n", "pkill", "-INT", "-P", str(pid), "-x", "tcpdump"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def stop(self, timeout=None):
        if self._process is None:
            return self.status()
        timeout = self.stop_timeout if timeout is None else timeout
        process = self._process
        if process.poll() is None:
            # sudo 在没有终端时不会把 SIGINT 转发给 tcpdump，实测会让停止卡满
            # 超时；因此先发给 sudo，再补发给真正的 tcpdump（子进程 /proc 在
            # root 进程上不可读，所以 sudo 场景改用 pkill 按 PCAP 路径精确匹配）。
            self._signal(process, signal.SIGINT)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and process.poll() is None:
                self._interrupt_child()
                for child in self._children(process):
                    self._signal(child, signal.SIGINT)
                time.sleep(0.1)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.ended_unix = self.wall()
        self._process = None
        if self._log is not None:
            self._log.flush()
            self._log.close()
            self._log = None
        self._parse_stats()
        self.state = "stopped"
        self.session.event("lidar_stopped", pcap_bytes=self.pcap.stat().st_size if self.pcap.exists() else 0,
                           **self.stats)
        return self.status()

    def _parse_stats(self):
        try:
            text = self.log_path.read_text("utf-8", errors="replace")
        except OSError:
            return
        for key, pattern in _STAT_PATTERNS.items():
            found = pattern.search(text)
            if found:
                self.stats[key] = int(found.group(1))

    def status(self):
        size = self.pcap.stat().st_size if self.pcap.exists() else 0
        running = self._process is not None and self._process.poll() is None
        return {
            "state": "live" if running else ("failed" if self._process is not None else self.state),
            "iface": self.iface,
            "port": self.port,
            "pcap": str(self.pcap),
            "pcap_bytes": size,
            "started_unix": self.started_unix,
            "ended_unix": self.ended_unix,
            **self.stats,
        }
