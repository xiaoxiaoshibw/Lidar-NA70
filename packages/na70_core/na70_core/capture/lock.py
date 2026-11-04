"""Cross-process exclusive lease for recorders sharing one session root."""
from __future__ import annotations

import os


class CaptureLock:
    def __init__(self, root):
        self.path = root / ".capture.lock"
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            raise RuntimeError("采集锁已持有")
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("已有采集进程使用此会话目录") from exc
        self.handle = handle

    def release(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
