#!/usr/bin/env python3
"""诊断 NA70 扫描结构：包间角度连续性、帧间点云重叠率。

用于判断：
  1. 100 包是否构成一个完整扫描周期（帧间体素是否可重复）；
  2. 若不可重复，应如何组织运动检测。
"""
import struct
import sys

import numpy as np

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 16.0 / 1000.0
FRAME_PKTS = 100


def iter_udp_payloads(path):
    with open(path, "rb") as f:
        hdr = f.read(24)
        magic, = struct.unpack("<I", hdr[:4])
        endian = "<" if magic in (0xA1B2C3D4, 0xA1B23C4D) else ">"
        ns = magic in (0xA1B23C4D, 0x4D3CB2A1)
        first = None
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                break
            ts_sec, ts_frac, caplen, _ = struct.unpack(endian + "IIII", ph)
            data = f.read(caplen)
            if len(data) < caplen:
                break
            ts = ts_sec + (ts_frac / 1e9 if ns else ts_frac / 1e6)
            if first is None:
                first = ts
            if len(data) < 42 or struct.unpack(">H", data[12:14])[0] != 0x0800:
                continue
            ihl = (data[14] & 0x0F) * 4
            if data[14 + 9] != 17:
                continue
            ulen = struct.unpack(">H", data[14 + ihl + 4:14 + ihl + 6])[0]
            yield ts - first, data[14 + ihl + 8:14 + ihl + ulen]


def pkt_codes(payload):
    b = np.frombuffer(payload[:1400], dtype=np.uint8).reshape(-1, 7)
    d = (b[:, 0].astype(np.uint32) << 8) | b[:, 1]
    p = (b[:, 2].astype(np.uint32) << 8) | b[:, 3]
    y = (b[:, 4].astype(np.uint32) << 8) | b[:, 5]
    r = b[:, 6]
    return d, p, y, r


def to_xyz(d, p, y):
    d = d.astype(np.float64) * DIST_RES_M
    p = p.astype(np.float64) * PITCH_RES * np.pi / 180
    y = y.astype(np.float64) * YAW_RES * np.pi / 180
    x = d * np.cos(y) * np.cos(p)
    yy = d * np.sin(y) * np.cos(p)
    z = d * np.sin(p)
    return np.column_stack([x, yy, z])


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Code\radar\radar_capture_data\2.pcap"
    payloads = []
    for _ts, p in iter_udp_payloads(path):
        if len(p) >= 1410:
            payloads.append(p)
        if len(payloads) >= 260:
            break

    # 1) 每 20 包的航向/俯仰覆盖
    print("== 前 260 包，每 20 包的角度码覆盖 ==")
    for i in range(0, 260, 20):
        codes = [pkt_codes(p) for p in payloads[i:i + 20]]
        y_min = min(c[2].min() for c in codes)
        y_max = max(c[2].max() for c in codes)
        p_min = min(c[1].min() for c in codes)
        p_max = max(c[1].max() for c in codes)
        print(f"包 {i:3d}~{i+19:3d}: 航向码 {y_min:5d}~{y_max:5d} "
              f"({y_min*YAW_RES:5.1f}°~{y_max*YAW_RES:5.1f}°) "
              f"俯仰码 {p_min:4d}~{p_max:4d} ({p_min*PITCH_RES:5.1f}°~{p_max*PITCH_RES:5.1f}°)")

    # 2) 每包角度起点序列（看是否周期性）
    print("\n== 连续 60 包：每包航向起点/终点 ==")
    for i in range(60):
        d, p, y, r = pkt_codes(payloads[i])
        print(f"包{i:3d}: yaw {y.min():5d}~{y.max():5d} pitch {p.min():4d}~{p.max():4d} "
              f"dist {d.min():5d}~{d.max():5d} refl>0={100*(r>0).mean():.0f}%")

    # 3) 帧间体素重叠率：帧0 vs 帧1, 帧0 vs 帧50（同一"相位"）
    def frame_pts(start, n=FRAME_PKTS):
        parts = [to_xyz(*pkt_codes(p)[:3]) for p in payloads[start:start + n]]
        return np.vstack(parts)

    def vox_overlap(a, b):
        va = np.unique(np.floor(a / 0.15).astype(np.int64), axis=0)
        vb = np.unique(np.floor(b / 0.15).astype(np.int64), axis=0)
        sa = set(map(tuple, va))
        sb = set(map(tuple, vb))
        inter = len(sa & sb)
        return inter / max(len(sa), len(sb)), len(sa), len(sb)

    f0 = frame_pts(0)
    f1 = frame_pts(100)
    f2 = frame_pts(200)
    o01, n0, n1 = vox_overlap(f0, f1)
    o02, _, n2 = vox_overlap(f0, f2)
    o12, _, _ = vox_overlap(f1, f2)
    print(f"\n== 帧间体素重叠（0.15m 体素）==")
    print(f"帧0({n0}体素) vs 帧1({n1}体素): IoU-ish={o01:.3f}")
    print(f"帧0 vs 帧2({n2}体素): IoU-ish={o02:.3f}")
    print(f"帧1 vs 帧2: IoU-ish={o12:.3f}")

    # 4) 如果帧间几乎不重叠，检查是否每帧扫不同相位：比较帧0前50包 与 帧1前50包的角度
    #    若帧1前50包 ≈ 帧0后50包 → 扫描是接力的，帧=完整周期
    d0, p0, y0, r0 = zip(*[pkt_codes(p) for p in payloads[0:50]])
    d1, p1, y1, r1 = zip(*[pkt_codes(p) for p in payloads[50:100]])
    d2, p2, y2, r2 = zip(*[pkt_codes(p) for p in payloads[100:150]])
    y0a = np.concatenate([c for c in y0]); y0b = np.concatenate([c for c in y1])
    y1a = np.concatenate([c for c in y2])
    print(f"\n== 扫描相位检查 ==")
    print(f"帧0前50包 航向码: {y0a.min()}~{y0a.max()}  中位 {np.median(y0a):.0f}")
    print(f"帧0后50包 航向码: {y0b.min()}~{y0b.max()}  中位 {np.median(y0b):.0f}")
    print(f"帧1前50包 航向码: {y1a.min()}~{y1a.max()}  中位 {np.median(y1a):.0f}")


if __name__ == "__main__":
    main()
