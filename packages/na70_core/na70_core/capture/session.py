"""采集会话目录：独占创建、事件流、文件哈希与 manifest/quality 落盘。

约定（与 ``Plan/NA70_YESENSE_标定AI执行计划.md`` 第 6 节一致）：

* 会话目录**只创建一次**，同名目录已存在直接报错，绝不覆盖。
* 每个会话至少包含 manifest.json / events.jsonl / quality.json；
  raw/ 与导出文件按实际取得的设备创建。
* manifest 记录每个文件的字节数与 SHA-256，供传输后对账。
* 未知信息保留 ``None``，不填零、不猜单位。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import sys
import time
from pathlib import Path

SCHEMA = "na70-capture-session-v1"
MANIFEST = "manifest.json"
QUALITY = "quality.json"
EVENTS = "events.jsonl"
OPERATOR = "operator.json"


class CaptureSessionError(RuntimeError):
    pass


def _iso(unix):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(unix)) + time.strftime("%z", time.localtime(unix))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class CaptureSession:
    """一次原始采集的落盘目录与会话级元数据。"""

    def __init__(self, root, *, session_id=None, operator=None, task=None,
                 min_free_bytes=2 * 1024 ** 3, clock=time.time):
        self.root = Path(root)
        self.clock = clock
        self.min_free_bytes = min_free_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        session_id = session_id or time.strftime("%Y%m%d-%H%M%S")
        self.session_id = session_id
        self.path = self.root / session_id
        try:
            self.path.mkdir()
        except FileExistsError as exc:
            raise CaptureSessionError(f"会话目录已存在，禁止覆盖：{self.path}") from exc
        self.created_unix = clock()
        self._events = None
        self._closed = False
        self.operator = dict(operator or {})
        self.operator.setdefault("task", task)
        self.write_json(OPERATOR, {
            "session_id": session_id,
            "created_unix": self.created_unix,
            "created_local": _iso(self.created_unix),
            "operator": self.operator,
            "unknown_fields": "留 None 表示未测/未知，不填估算值",
        })
        self._events = self.open(EVENTS, "a", text=True)
        self.event("session_created", session_id=session_id)

    # ---------------------------------------------------------------- paths
    def rel(self, *parts):
        return "/".join(str(part) for part in parts)

    def file(self, *parts):
        target = self.path.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def open(self, relative, mode="wb", text=False):
        target = self.file(relative)
        if "b" not in mode and not text:
            mode += "b"
        return open(target, mode, encoding="utf-8" if text else None)

    def write_json(self, relative, payload):
        target = self.file(relative)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
                          encoding="utf-8")
        return target

    # --------------------------------------------------------------- events
    def event(self, kind, **fields):
        record = {"t_unix": self.clock(), "kind": kind}
        for key, value in fields.items():
            if isinstance(value, float):
                record[key] = round(value, 6)
            else:
                record[key] = value
        self._events.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._events.flush()
        return record

    # ----------------------------------------------------------------- disk
    def free_bytes(self):
        return shutil.disk_usage(self.path).free

    def ensure_disk(self, minimum=None):
        available = self.free_bytes()
        required = self.min_free_bytes if minimum is None else minimum
        if available < required:
            raise CaptureSessionError(
                f"磁盘剩余 {available / 1024 ** 2:.0f} MiB 低于要求 {required / 1024 ** 2:.0f} MiB")
        return available

    # -------------------------------------------------------------- finalize
    def file_inventory(self):
        entries = []
        for path in sorted(self.path.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.path).as_posix()
            if relative == MANIFEST:
                continue
            entries.append({"path": relative, "bytes": path.stat().st_size,
                            "sha256": sha256_file(path)})
        return entries

    def host_info(self):
        return {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        }

    def finalize(self, *, status, quality, devices=None, windows=None, notes=None):
        if self._closed:
            raise CaptureSessionError("会话已结束")
        self.write_json(QUALITY, quality)
        self.event("session_finalized", status=status)
        self._events.close()
        self._events = None
        self._closed = True
        manifest = {
            "schema": SCHEMA,
            "session_id": self.session_id,
            "created_unix": self.created_unix,
            "created_local": _iso(self.created_unix),
            "finalized_unix": self.clock(),
            "status": status,
            "host": self.host_info(),
            "operator": self.operator,
            "devices": devices or {},
            "windows": windows or {},
            "notes": list(notes or []),
            "files": self.file_inventory(),
        }
        self.write_json(MANIFEST, manifest)
        return manifest

    def close(self):
        if self._events is not None:
            self._events.close()
            self._events = None
        self._closed = True
