"""SYNTHETIC wire-level regression, plus optional real captures in this repo."""
from pathlib import Path
import struct

import numpy as np
import pytest

from backend.decoder import CaptureError, MAGICS, decode_frame, decode_frame_codes, device_time, scan_pcap


def payload(distance=1000, pitch=0, yaw=0, intensity=77, factory=0):
    return struct.pack(">HHHB", distance, pitch, yaw, intensity) * 200 + b"\0\0\0\0\0\x01\0\x7b" + struct.pack("<H", factory)


def make_pcap(path, payloads, magic=b"\xd4\xc3\xb2\xa1", port=2368, timestamps=None):
    endian, scale = MAGICS[magic]
    data = bytearray(magic + struct.pack(endian + "HHIIII", 2, 4, 0, 0, 65535, 1))
    for i, p in enumerate(payloads):
        ether = b"\0" * 12 + b"\x08\0"
        ip = struct.pack(">BBHHHBBHII", 0x45, 0, 28 + len(p), 0, 0, 64, 17, 0, 0, 0)
        udp = struct.pack(">HHHH", 7000, port, 8 + len(p), 0)
        packet = ether + ip + udp + p
        timestamp = timestamps[i] if timestamps else i / 1000
        sec = int(timestamp)
        data.extend(struct.pack(endian + "IIII", sec + 1700000000, round((timestamp - sec) * scale), len(packet), len(packet)))
        data.extend(packet)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("magic", list(MAGICS))
def test_container_precision_and_incomplete_tail(tmp_path, magic):
    p = make_pcap(tmp_path / "sample.pcap", [payload()] * 101, magic)
    scan = scan_pcap(p)
    assert len(scan.frames) == 2 and scan.valid_packets == 101
    assert scan.frames[0]["complete"] and not scan.frames[1]["complete"]
    assert scan.duration == pytest.approx(.1, abs=1e-6)
    points = decode_frame(p, scan.frames[0])
    assert points.shape == (20000, 4)
    np.testing.assert_array_equal(points[0], [0, -16, 0, 77])
    np.testing.assert_array_equal(decode_frame_codes(p, scan.frames[0])[0], [1000, 0, 0, 77])
    assert scan.frames[0]["device_time"] == 1.123


def test_factory_at_end_rotates_entire_bundle_and_survives_seek(tmp_path):
    # 20 degree Z offset on last packet in first frame; inherited by next frame.
    data = [payload()] * 199 + [payload(factory=0x1000 | (2048 + 500))]
    data[99] = payload(factory=0x2000 | (2048 + 2000))
    p = make_pcap(tmp_path / "factory.pcap", data)
    scan = scan_pcap(p)
    first = decode_frame(p, scan.frames[0])
    theta = np.deg2rad(20)
    np.testing.assert_allclose(first[0, :3], [16 * np.sin(theta), -16 * np.cos(theta), 0], atol=1e-5)
    np.testing.assert_array_equal(first[0], first[-1])
    second = decode_frame(p, scan.frames[1])
    pitch = np.deg2rad(5)
    np.testing.assert_allclose(second[0, :3], [16*np.sin(theta)*np.cos(pitch), -16*np.cos(theta), -16*np.sin(theta)*np.sin(pitch)], atol=1e-5)
    np.testing.assert_array_equal(first, decode_frame(p, scan.frames[0]))


@pytest.mark.parametrize("data", [b"", b"not a pcap" * 4, b"\x0a\x0d\x0d\x0a" + b"\0" * 30])
def test_bad_files(tmp_path, data):
    p = tmp_path / "bad.pcap"; p.write_bytes(data)
    with pytest.raises(CaptureError): scan_pcap(p)


def test_unrelated_port_and_truncation(tmp_path):
    p = make_pcap(tmp_path / "wrong.pcap", [payload()], port=1234)
    with pytest.raises(CaptureError, match="未找到"): scan_pcap(p)
    p = make_pcap(tmp_path / "cut.pcap", [payload()] * 102)
    p.write_bytes(p.read_bytes()[:-20])
    result = scan_pcap(p)
    assert result.valid_packets == 101 and result.invalid_packets == 1
    assert result.warnings and not result.frames[-1]["complete"]


def test_cancel_and_backward_timestamps(tmp_path):
    p = make_pcap(tmp_path / "time.pcap", [payload()] * 3, timestamps=[1, .5, 2])
    assert any("倒退" in w for w in scan_pcap(p).warnings)
    with pytest.raises(InterruptedError): scan_pcap(p, cancelled=lambda: True)


def test_device_time_invalid_and_gps():
    data = payload()
    assert device_time(data[:1400] + bytes([0x80 | 26, 9, 9, 0, 0, 0, 0, 0]) + b"\0\0") == 1788912000
    assert device_time(data[:1400] + bytes([0x80 | 26, 99, 9, 0, 0, 0, 0, 0]) + b"\0\0") is None
    assert device_time(data[:1406] + b"\xff\xff" + data[1408:]) is None


@pytest.mark.parametrize("name", ["1.pcap", "2.pcap"])
def test_real_captures_random_access(name):
    path = Path(__file__).resolve().parents[2] / "radar_capture_data" / name
    scan = scan_pcap(path)
    selected = [0, len(scan.frames) // 2, len(scan.frames) - 1]
    forward = {i: decode_frame(path, scan.frames[i]) for i in selected}
    for i in reversed(selected):
        np.testing.assert_array_equal(forward[i], decode_frame(path, scan.frames[i]))
        assert np.isfinite(forward[i]).all()
    assert scan.invalid_packets == 0
