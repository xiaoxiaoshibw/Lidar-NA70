#!/usr/bin/env python3
"""NA70 pcap 概况探查 v2：包结构、时间戳、码值分布。

用法：python analyze_pcap_overview.py <pcap>
"""
import struct
import sys
from collections import Counter

import numpy as np


def iter_udp(path):
    """yield (ts, payload) 每个 UDP 载荷。"""
    with open(path, "rb") as f:
        hdr = f.read(24)
        magic, = struct.unpack("<I", hdr[:4])
        endian, ns = ("<", False) if magic == 0xA1B2C3D4 else (">", False) \
            if magic == 0xD4C3B2A1 else ("<", True) if magic == 0xA1B23C4D else (">", True)
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
            if len(data) >= 42 and struct.unpack(">H", data[12:14])[0] == 0x0800:
                ihl = (data[14] & 0x0F) * 4
                if data[14 + 9] == 17:
                    ulen = struct.unpack(">H", data[14 + ihl + 4:14 + ihl + 6])[0]
                    yield ts - first, data[14 + ihl + 8:14 + ihl + ulen]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Code\radar\radar_capture_data\2.pcap"
    lens = Counter()
    na70 = []
    ts_all = []
    for ts, payload in iter_udp(path):
        lens[len(payload)] += 1
        if len(payload) >= 1410:
            na70.append(payload)
            ts_all.append(ts)

    print(f"UDP 载荷长度分布: {dict(lens)}")
    print(f"NA70 包数: {len(na70)}  抓包时长: {ts_all[-1]:.3f} s")
    if len(ts_all) > 1:
        dt = np.diff(ts_all)
        print(f"包间隔: 中位 {np.median(dt)*1e3:.3f} ms, 均值 {np.mean(dt)*1e3:.3f} ms, "
              f"min {dt.min()*1e3:.3f} ms, max {dt.max()*1e3:.3f} ms")
        # 时间戳字段采样
        tsb = np.frombuffer(b"".join(p[1400:1408] for p in na70[::500]), dtype="u1").reshape(-1, 8)
        sync = (tsb[:, 0] & 0x80) != 0
        print(f"时间戳同步标志: sync={sync.sum()} internal={(~sync).sum()} (采样 {len(tsb)})")
        # 内部时钟：6字节秒+2字节毫秒（大端）
        seconds = (tsb[:, 0].astype(np.uint64) & 0x7F) << 40
        for i in range(1, 6):
            seconds |= tsb[:, i].astype(np.uint64) << (8 * (5 - i))
        ms = (tsb[:, 6].astype(np.uint64) << 8) | tsb[:, 7]
        tdev = seconds + ms / 1000.0
        print(f"设备时间戳: 首={tdev[0]:.3f} 末={tdev[-1]:.3f} 跨度={tdev[-1]-tdev[0]:.3f} s")
        ddev = np.diff(tdev)
        print(f"设备时间戳间隔: 中位 {np.median(ddev)*1e3:.3f} ms 均值 {np.mean(ddev)*1e3:.3f} ms")
        # 设备时间戳 vs 抓包时间戳 是否线性
        pcap_dt = np.diff(ts_all[::500])
        print(f"同一批包 pcap 间隔: 中位 {np.median(pcap_dt)*1e3:.3f} ms")

    # 点字段码值分布（采样每200包）
    sample = na70[::200]
    rawb = np.frombuffer(b"".join(p[:1400] for p in sample), dtype="u1").reshape(-1, 7)
    raw = np.zeros((len(rawb), 3), dtype=np.uint16)
    raw[:, 0] = (rawb[:, 0].astype(np.uint16) << 8) | rawb[:, 1]  # 距离大端
    raw[:, 1] = (rawb[:, 2].astype(np.uint16) << 8) | rawb[:, 3]  # 俯仰大端
    raw[:, 2] = (rawb[:, 4].astype(np.uint16) << 8) | rawb[:, 5]  # 航向大端
    refl = rawb[:, 6]
    print(f"\n采样包数 {len(sample)}, 采样点数 {len(raw)}")
    print(f"{'字段':8s} {'min':>7s} {'max':>7s} {'mean':>9s} {'median':>7s} {'p1':>7s} {'p99':>7s} {'<32768%':>8s}")
    for name, col in zip(["距离码", "俯仰码", "航向码"], raw.T):
        print(f"{name:8s} {col.min():7d} {col.max():7d} {col.mean():9.1f} "
              f"{int(np.median(col)):7d} {int(np.percentile(col,1)):7d} {int(np.percentile(col,99)):7d} "
              f"{(100*(col<32768).mean()):8.1f}")
    print(f"反射率  min={refl.min()} max={refl.max()} mean={refl.mean():.1f} "
          f"median={int(np.median(refl))} 零占比={100*(refl==0).mean():.1f}%")
    # 按 0.01° 和 0.0258°/0.0432° 两种假设换算俯仰航向
    for name, col, res in zip(["俯仰", "航向"], raw[:, 1:].T, [0.0258, 0.0432]):
        deg = col.astype(np.float64) * res
        print(f"{name} ×{res}°: min={deg.min():.1f} max={deg.max():.1f} median={np.median(deg):.1f}")
    for name, col in zip(["俯仰", "航向"], raw[:, 1:].T):
        deg = col.astype(np.float64) * 0.01
        print(f"{name} ×0.01°: min={deg.min():.1f} max={deg.max():.1f} median={np.median(deg):.1f}")
    d_m = raw[:, 0].astype(np.float64) * 16.0 / 1000.0
    print(f"距离 ×16mm: min={d_m.min():.1f} max={d_m.max():.1f} median={np.median(d_m):.1f} m")
    d_m2 = raw[:, 0].astype(np.float64) * 0.01
    print(f"距离 ×0.01m: min={d_m2.min():.1f} max={d_m2.max():.1f} median={np.median(d_m2):.1f} m")
    # 一包内码值变化（看扫描结构）
    def _pkt_codes(payload):
        b = np.frombuffer(payload[:1400], dtype="u1").reshape(-1, 7)
        d = (b[:, 0].astype(np.uint16) << 8) | b[:, 1]
        p = (b[:, 2].astype(np.uint16) << 8) | b[:, 3]
        y = (b[:, 4].astype(np.uint16) << 8) | b[:, 5]
        return d, p, y

    p0 = _pkt_codes(na70[0])
    print(f"\n首包200点: 距离码 {p0[0].min()}~{p0[0].max()} 俯仰码 {p0[1].min()}~{p0[1].max()} "
          f"航向码 {p0[2].min()}~{p0[2].max()}")
    p1 = _pkt_codes(na70[1])
    print(f"次包200点: 距离码 {p1[0].min()}~{p1[0].max()} 俯仰码 {p1[1].min()}~{p1[1].max()} "
          f"航向码 {p1[2].min()}~{p1[2].max()}")


if __name__ == "__main__":
    main()
