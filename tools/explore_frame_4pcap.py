#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4.pcap 逐帧探查：按设备时间戳分组，统计每帧点数/距离/角度范围。"""
import struct
import sys

import numpy as np

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 0.01


def iter_udp(path):
    with open(path, "rb") as f:
        f.read(24)
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                break
            ts_sec, ts_frac, caplen, _ = struct.unpack("<IIII", ph)
            data = f.read(caplen)
            if len(data) < caplen:
                break
            if len(data) < 42 or struct.unpack(">H", data[12:14])[0] != 0x0800:
                continue
            ihl = (data[14] & 0x0F) * 4
            if data[14 + 9] != 17:
                continue
            ulen = struct.unpack(">H", data[14 + ihl + 4:14 + ihl + 6])[0]
            yield data[14 + ihl + 8:14 + ihl + ulen]


def dev_ts(pl):
    tsb = pl[1400:1408]
    seconds = (tsb[0] & 0x7F) << 40
    for i in range(1, 6):
        seconds |= tsb[i] << (8 * (5 - i))
    ms = (tsb[6] << 8) | tsb[7]
    return seconds + ms / 1000.0


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Code\radar\radar_capture_data\4.pcap"
    pkts = []  # (dev_ts, dcode, pcode, ycode, refl)
    for pl in iter_udp(path):
        if len(pl) < 1410:
            continue
        rb = np.frombuffer(pl[:1400], dtype=np.uint8).reshape(-1, 7)
        dcode = (rb[:, 0].astype(np.uint32) << 8) | rb[:, 1]
        pcode = (rb[:, 2].astype(np.uint32) << 8) | rb[:, 3]
        ycode = (rb[:, 4].astype(np.uint32) << 8) | rb[:, 5]
        refl = rb[:, 6]
        pkts.append((dev_ts(pl), dcode, pcode, ycode, refl))
    pkts.sort(key=lambda x: x[0])

    groups = []
    for p in pkts:
        if not groups or abs(p[0] - groups[-1][0][0]) > 0.25:
            groups.append([p])
        else:
            groups[-1].append(p)
    print(f"packets {len(pkts)}, frames {len(groups)}")
    print(f"{'frame':>5s} {'dev_ts(s)':>10s} {'pkts':>5s} {'valid':>8s} "
          f"{'dmax(m)':>8s} {'yaw(deg)':>14s} {'pitch(deg)':>12s} {'reflmax':>7s}")

    stat = []
    for i, g in enumerate(groups):
        n = sum(int((p[4] > 0).sum()) for p in g)
        dc = np.concatenate([p[1] for p in g])
        yc = np.concatenate([p[2] for p in g])
        pc = np.concatenate([p[3] for p in g])
        rf = np.concatenate([p[4] for p in g])
        v = rf > 0
        if not v.any():
            print(f"{i:5d} {g[0][0]:10.3f} {len(g):5d} {0:8d} {'-':>8s} {'-':>14s} {'-':>12s} {0:7d}")
            continue
        dmax = dc[v].max() * DIST_RES_M
        ymin, ymax = yc[v].min() * YAW_RES, yc[v].max() * YAW_RES
        pmin, pmax = pc[v].min() * PITCH_RES, pc[v].max() * PITCH_RES
        print(f"{i:5d} {g[0][0]:10.3f} {len(g):5d} {n:8d} {dmax:8.2f} "
              f"{ymin:6.1f}~{ymax:6.1f} {pmin:5.1f}~{pmax:5.1f} {int(rf.max()):7d}")
        stat.append((i, g[0][0], n, dmax, ymin, ymax))
    np.save(r"D:\Code\radar\tools\_frame_stat.npy", np.array(stat, dtype=float))


if __name__ == "__main__":
    main()
