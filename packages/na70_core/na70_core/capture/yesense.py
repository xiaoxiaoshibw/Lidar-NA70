"""YESENSE IMU 帧解析（无 I/O、无 numpy）。

线格式已在两处独立核对：归档原型
``Plan/evidence/slam/20261030-imu-mounting/monitor-source/monitor.py`` 与
2026-10-30 Jetson 实测链路。实测事实（6 秒、约 1200 帧）：

* 固定 67 字节帧：``YS``(2) + tid(2) + LEN(1) + payload(LEN) + checksum(2)；
  长度字段在 ``buf[4]``，整帧长度 = ``LEN + 7``。
* checksum 为 8 位 Fletcher 变体：对 ``raw[2:-2]`` 累计 sum-a/sum-b。
* ``tid`` 为 16 位小端；监视器原型与现场实测均见 60000→1，
  另保留 16 位回绕路径。仅靠序号无法证明设备没有丢失数据。
* payload 为若干 ``kind`` + ``length`` + 小端 int32 组，实测只出现
  0x10 加速度 / 0x20 角速度 / 0x40 欧拉角 / 0x41 四元数。
* 解码值统一乘 1e-6。加速度实测模长 9.77 → 单位 m/s²；角速度与欧拉角
  变化同量级、静态幅度约 0.1 deg/s → 单位为 deg/s（推断，六面/转台再确认）。
* 解析器支持 0x51/0x52 设备时间字段；上述实测与 20261104-170341
  会话未见这些字段，不能据此断言设备或固件永远不会输出。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEADER = b"YS"
FRAME_OVERHEAD = 7
SCALE = 1e-6

ACCEL_GROUP = 0x10
GYRO_GROUP = 0x20
EULER_GROUP = 0x40
QUATERNION_GROUP = 0x41
SAMPLE_TIME_GROUP = 0x51
READY_TIME_GROUP = 0x52

_VECTOR_GROUPS = {
    ACCEL_GROUP: ("accel", 12),
    GYRO_GROUP: ("gyro", 12),
    EULER_GROUP: ("euler", 12),
    QUATERNION_GROUP: ("quaternion", 16),
}
_TIME_GROUPS = {SAMPLE_TIME_GROUP: "sample_us", READY_TIME_GROUP: "ready_us"}


@dataclass(frozen=True)
class ImuFrame:
    """一条已校验的 IMU 帧；缺失的字段保持 ``None``，不填默认值。"""

    tid: int
    accel: tuple = None
    gyro: tuple = None
    euler: tuple = None
    quaternion: tuple = None
    sample_us: int = None
    ready_us: int = None
    raw: bytes = field(default=b"", repr=False)

    @property
    def has_motion(self):
        return self.accel is not None or self.gyro is not None


def next_tid(tid):
    return (tid + 1) & 0xFFFF


def tid_follows(previous, current):
    """接受设备实测 60000→1 及常规 16 位递增；不是零丢帧证明。"""
    return current == next_tid(previous) or (previous == 60000 and current == 1)


def checksum(raw):
    a = b = 0
    for value in raw[2:-2]:
        a = (a + value) & 0xFF
        b = (b + a) & 0xFF
    return bytes((a, b))


class FrameParser:
    """有状态字节流解析器：可跨任意接收块边界。

    校验失败只丢 1 字节并重新同步，原始字节仍由调用方另存，因此“拒绝解码”
    不等于“丢证据”。
    """

    def __init__(self):
        self._buffer = bytearray()
        self.bad_checksum = 0
        self.bad_payload = 0
        self.discarded_bytes = 0

    def reset(self):
        """断流重连后清空半帧缓冲，并把残留字节计入丢弃量。"""
        self.discarded_bytes += len(self._buffer)
        self._buffer.clear()

    @property
    def pending_bytes(self):
        return len(self._buffer)

    def feed(self, data):
        self._buffer.extend(data)
        frames = []
        while len(self._buffer) >= FRAME_OVERHEAD:
            start = self._buffer.find(HEADER)
            if start < 0:
                keep = len(self._buffer) - 1
                self.discarded_bytes += keep
                del self._buffer[:keep]
                break
            if start:
                self.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < FRAME_OVERHEAD:
                break
            size = self._buffer[4] + FRAME_OVERHEAD
            if len(self._buffer) < size:
                break
            raw = bytes(self._buffer[:size])
            if raw[-2:] != checksum(raw):
                self.bad_checksum += 1
                del self._buffer[0]
                continue
            del self._buffer[:size]
            frame = self._decode(raw)
            if frame is None:
                self.bad_payload += 1
                continue
            frames.append(frame)
        return frames

    @staticmethod
    def _decode(raw):
        payload = raw[5:-2]
        position = 0
        values = {}
        while position < len(payload):
            if position + 2 > len(payload):
                return None
            kind = payload[position]
            length = payload[position + 1]
            value = payload[position + 2:position + 2 + length]
            if len(value) != length:
                return None
            if kind in _VECTOR_GROUPS:
                name, expected = _VECTOR_GROUPS[kind]
                if length != expected:
                    return None
                count = length // 4
                values[name] = tuple(v * SCALE for v in struct.unpack("<" + "i" * count, value))
            elif kind in _TIME_GROUPS:
                if length != 4:
                    return None
                values[_TIME_GROUPS[kind]] = int.from_bytes(value, "little")
            position += length + 2
        tid = int.from_bytes(raw[2:4], "little")
        return ImuFrame(tid=tid, raw=raw, **values)
