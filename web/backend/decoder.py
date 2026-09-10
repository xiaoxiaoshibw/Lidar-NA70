"""NA70 wire decoder, aligned to double_wedge_driver (normal installation).

The driver applies the LAST factory offsets in each 100-packet bundle to ALL
points in that bundle. Saving the final state, rather than rotating each packet
immediately, is essential for seek/sequential equivalence.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import struct
from pathlib import Path
from typing import Callable

import numpy as np

DECODER_VERSION = "ros2-na70-16mm-v1"
MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e6), b"\xa1\xb2\xc3\xd4": (">", 1e6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e9), b"\xa1\xb2\x3c\x4d": (">", 1e9),
}


class CaptureError(ValueError):
    pass


@dataclass
class Scan:
    frames: list = field(default_factory=list)
    valid_packets: int = 0
    invalid_packets: int = 0
    ignored_packets: int = 0
    warnings: list = field(default_factory=list)
    duration: float = 0.0


def device_time(payload: bytes):
    b = payload[1400:1408]
    millis = int.from_bytes(b[6:8], "big")
    if millis >= 1000:
        return None
    if b[0] & 128:
        try:
            return datetime(2000 + (b[0] & 127), *b[1:6], tzinfo=timezone.utc).timestamp() + millis / 1000
        except ValueError:
            return None
    seconds = int.from_bytes(b[:6], "big")
    return seconds + millis / 1000 if seconds <= 0xFFFFFFFF else None


def udp_payload(packet: bytes):
    """Return (payload offset, payload) or None for unrelated traffic."""
    if len(packet) < 14:
        raise CaptureError("以太网头截断")
    ethertype = int.from_bytes(packet[12:14], "big")
    start = 14
    while ethertype in (0x8100, 0x88A8):
        if len(packet) < start + 4:
            raise CaptureError("VLAN 头截断")
        ethertype = int.from_bytes(packet[start + 2:start + 4], "big")
        start += 4
    if ethertype != 0x0800:
        return None
    if len(packet) < start + 20:
        raise CaptureError("IPv4 头截断")
    ihl = (packet[start] & 15) * 4
    total = int.from_bytes(packet[start + 2:start + 4], "big")
    if packet[start] >> 4 != 4 or ihl < 20 or total < ihl or len(packet) < start + total:
        raise CaptureError("IPv4 长度无效或截断")
    if packet[start + 9] != 17:
        return None
    if int.from_bytes(packet[start + 6:start + 8], "big") & 0x3FFF:
        raise CaptureError("不支持分片 UDP")
    offset = start + ihl
    if total < ihl + 8:
        raise CaptureError("UDP 头截断")
    _, dest, length, _ = struct.unpack_from(">HHHH", packet, offset)
    if dest != 2368:
        return None
    if length < 8 or length > total - ihl:
        raise CaptureError("UDP 长度无效")
    payload = packet[offset + 8:offset + length]
    if len(payload) != 1410:
        raise CaptureError("2368 端口数据长度不是 NA70 的 1410 字节")
    return offset + 8, payload


def scan_pcap(path: Path, progress: Callable = lambda *_: None, cancelled: Callable = lambda: False) -> Scan:
    result = Scan()
    size = path.stat().st_size
    pitch_offset = yaw_offset = 0.0
    packets = []
    first_time = None
    last_time = None
    previous_play_time = 0.0
    nonmonotonic = 0
    bad_device_times = 0

    def finish():
        nonlocal packets, previous_play_time
        if not packets:
            return
        relative = packets[0][1] - first_time
        play_time = max(previous_play_time, relative)
        previous_play_time = play_time
        result.frames.append(dict(
            index=len(result.frames), capture_time=packets[0][1], end_time=packets[-1][1],
            time=play_time, device_time=packets[-1][2], device_start_time=packets[0][2],
            frame_id="lidar_link", point_count=len(packets) * 200, complete=len(packets) == 100,
            packet_count=len(packets), offsets=[p[0] for p in packets],
            pitch_offset=pitch_offset, yaw_offset=yaw_offset,
        ))
        packets = []

    with path.open("rb") as source:
        header = source.read(24)
        if len(header) < 24:
            raise CaptureError("文件为空或 PCAP 文件头不足 24 字节")
        if header[:4] not in MAGICS:
            raise CaptureError("不支持该格式；请选择经典 .pcap 文件（暂不支持 pcapng）")
        endian, scale = MAGICS[header[:4]]
        major, minor, _, _, snaplen, network = struct.unpack(endian + "HHIIII", header[4:])
        if (major, minor) != (2, 4) or network != 1:
            raise CaptureError("仅支持 PCAP 2.4 / Ethernet 链路类型")
        while True:
            if cancelled():
                raise InterruptedError("导入已取消")
            record = source.read(16)
            if not record:
                break
            if len(record) != 16:
                result.invalid_packets += 1
                result.warnings.append("文件尾部的记录头截断，已保留前面的完整记录")
                break
            sec, fraction, captured, original = struct.unpack(endian + "IIII", record)
            offset = source.tell()
            if captured > size - offset or captured > snaplen or captured > 16 * 1024 * 1024 or captured > original:
                result.invalid_packets += 1
                result.warnings.append("记录长度无效或文件截断，扫描已停止")
                break
            packet = source.read(captured)
            progress(source.tell() / size, result)
            if captured != original or fraction >= scale:
                result.invalid_packets += 1
                continue
            try:
                parsed = udp_payload(packet)
            except CaptureError:
                result.invalid_packets += 1
                continue
            if parsed is None:
                result.ignored_packets += 1
                continue
            payload_offset, payload = parsed
            timestamp = sec + fraction / scale
            if first_time is None:
                first_time = timestamp
            if last_time is not None and timestamp < last_time:
                nonmonotonic += 1
            last_time = timestamp
            factory = int.from_bytes(payload[1408:1410], "little")
            if factory >> 12 == 1:
                pitch_offset = float(np.float32((factory & 4095) - 2048) * np.float32(0.01))
            elif factory >> 12 == 2:
                yaw_offset = float(np.float32((factory & 4095) - 2048) * np.float32(0.01))
            dev = device_time(payload)
            bad_device_times += dev is None
            packets.append((offset + payload_offset, timestamp, dev))
            result.valid_packets += 1
            if len(packets) == 100:
                finish()
        finish()
    if not result.frames:
        raise CaptureError(f"未找到有效 NA70 UDP 数据（目标端口 2368）；异常记录 {result.invalid_packets} 条")
    if nonmonotonic:
        result.warnings.append(f"发现 {nonmonotonic} 个倒退时间戳；保留包顺序，播放时间轴单调化")
    if bad_device_times:
        result.warnings.append(f"{bad_device_times} 个设备时间无效，元数据标记为空，回放使用捕获时间")
    if result.invalid_packets:
        result.warnings.append(f"跳过或停止处理的异常记录共 {result.invalid_packets} 条")
    result.duration = max(result.frames[-1]["time"], (last_time or 0) - (first_time or 0))
    return result


def decode_frame(path: Path, frame: dict, install_direction: int = 1) -> np.ndarray:
    if install_direction not in (1, 2):
        raise ValueError("安装方向必须为 1 或 2")
    with path.open("rb") as source:
        payloads = []
        for offset in frame["offsets"]:
            source.seek(offset)
            payload = source.read(1400)
            if len(payload) != 1400:
                raise CaptureError("缓存文件已截断，请重新导入")
            payloads.append(payload)
    b = np.frombuffer(b"".join(payloads), dtype=np.uint8).reshape(-1, 7)
    code = lambda i: ((b[:, i].astype(np.uint16) << 8) | b[:, i + 1]).astype(np.float32)
    distance = code(0) * np.float32(16) / np.float32(1000)
    pitch = code(2) * np.float32(0.0258) * np.float32(np.pi) / np.float32(180)
    yaw = code(4) * np.float32(0.0432) * np.float32(np.pi) / np.float32(180)
    x = distance * np.cos(yaw) * np.cos(pitch)
    y = distance * np.sin(yaw) * np.cos(pitch)
    z = distance * np.sin(pitch)
    xyz = np.column_stack((z, -x, -y) if install_direction == 1 else (z, x, y))
    p = np.float32(frame["pitch_offset"]) * np.float32(np.pi) / np.float32(180)
    w = np.float32(frame["yaw_offset"]) * np.float32(np.pi) / np.float32(180)
    cp, sp, cw, sw = np.cos(p), np.sin(p), np.cos(w), np.sin(w)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=np.float32)
    rz = np.array([[cw, -sw, 0], [sw, cw, 0], [0, 0, 1]], dtype=np.float32)
    rotation = ry @ rz
    if install_direction == 2:
        a = np.float32(np.pi)
        rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]], dtype=np.float32)
        rotation = rx @ rotation
    points = np.empty((len(b), 4), dtype="<f4")
    points[:, :3] = xyz @ rotation.T
    points[:, 3] = b[:, 6]
    return points


def public_frame(frame):
    return {k: v for k, v in frame.items() if k != "offsets"}
