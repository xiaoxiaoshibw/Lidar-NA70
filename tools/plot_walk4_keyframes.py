#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画 4.pcap 关键帧的 (yaw, pitch, range) 散点，观察行人信号形态。"""
import struct
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

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
            _, _, caplen, _ = struct.unpack("<IIII", ph)
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
    pkts = []
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
    frames = []
    for p in pkts:
        if not frames or abs(p[0] - frames[-1][0][0]) > 0.25:
            frames.append([p])
        else:
            frames[-1].append(p)
    print(f"frames {len(frames)}")

    T = []
    for i, g in enumerate(frames):
        T.append(g[0][0])
    T = np.array(T)
    tmin = T.min()

    want = [5, 15, 25, 35, 45, 55, 65, 80]  # 帧号
    n = len(want)
    cols = 4
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 5.5, rows * 4.2))
    axes = np.atleast_1d(axes).ravel()
    for k, fi in enumerate(want):
        g = frames[fi]
        d = np.concatenate([p[1] for p in g]).astype(np.float32) * DIST_RES_M
        p = np.concatenate([p[2] for p in g]).astype(np.float32) * PITCH_RES
        y = np.concatenate([p[3] for p in g]).astype(np.float32) * YAW_RES
        rf = np.concatenate([p[4] for p in g]).astype(np.uint8)
        m = (rf > 0) & (d >= 0.5) & (d <= 50)
        ax = axes[k]
        sc = ax.scatter(y[m], p[m], c=d[m], s=1.2, cmap="turbo",
                        vmin=0, vmax=12, alpha=0.55)
        ax.set_xlim(85, 160)
        ax.set_ylim(0, 220)
        ax.set_xlabel("yaw (°)")
        ax.set_ylabel("pitch (°)")
        ax.set_title(f"帧{fi}  t={T[fi]-tmin:.2f}s  n={int(m.sum())}")
        ax.grid(True, alpha=0.3)
    fig.colorbar(sc, ax=axes, label="range (m)", shrink=0.6)
    fig.suptitle("4.pcap 关键帧点云 (yaw-pitch, 颜色=距离)")
    fig.tight_layout()
    out = r"D:\Code\radar\tools\analysis_output\fig_walk4_keyframes.png"
    fig.savefig(out, dpi=120)
    print("saved", out)


if __name__ == "__main__":
    main()
