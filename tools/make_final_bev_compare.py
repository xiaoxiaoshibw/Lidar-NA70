#!/usr/bin/env python3
"""NA70 移动目标判定 —— 最终 BEV 前后对比图（0-18s vs 35-70s vs 差值）。"""
import struct
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

PATH = r"D:\Code\radar\radar_capture_data\2.pcap"
OUT = r"D:\Code\radar\tools\analysis_output\fig_final_bev_compare.png"
N, S = 200, 0.5


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
            a, b, c, _ = struct.unpack(endian + "IIII", ph)
            data = f.read(c)
            if len(data) < c:
                break
            ts = a + (b / 1e9 if ns else b / 1e6)
            if first is None:
                first = ts
            if len(data) < 42 or struct.unpack(">H", data[12:14])[0] != 0x0800:
                continue
            ihl = (data[14] & 0x0F) * 4
            if data[14 + 9] != 17:
                continue
            ulen = struct.unpack(">H", data[14 + ihl + 4:14 + ihl + 6])[0]
            yield ts - first, data[14 + ihl + 8:14 + ihl + ulen]


def grid_for(t0, t1):
    s1 = np.zeros(N * N)
    c = np.zeros(N * N)
    zmax = np.full(N * N, -np.inf)
    zmin = np.full(N * N, np.inf)
    for ts, p in iter_udp_payloads(PATH):
        if len(p) < 1410:
            continue
        if not (t0 <= ts <= t1):
            continue
        b = np.frombuffer(p[:1400], dtype=np.uint8).reshape(-1, 7)
        dcode = (b[:, 0].astype(np.uint32) << 8) | b[:, 1]
        pcode = (b[:, 2].astype(np.uint32) << 8) | b[:, 3]
        ycode = (b[:, 4].astype(np.uint32) << 8) | b[:, 5]
        refl = b[:, 6]
        if not (dcode.any() or pcode.any() or ycode.any()):
            continue
        d = dcode.astype(np.float32) * 0.01
        pitch = pcode.astype(np.float32) * 0.0258 * np.pi / 180
        yaw = ycode.astype(np.float32) * 0.0432 * np.pi / 180
        ok = (refl > 0) & (d >= 1.0) & (d <= 100)
        if not ok.any():
            continue
        x = d * np.cos(yaw) * np.cos(pitch)
        y = d * np.sin(yaw) * np.cos(pitch)
        z = d * np.sin(pitch)
        xb = np.floor((x + 50) / S).astype(np.int64)
        yb = np.floor((y + 50) / S).astype(np.int64)
        okg = (xb >= 0) & (xb < N) & (yb >= 0) & (yb < N)
        idx = yb[okg] * N + xb[okg]
        dg = d[okg]
        zg = z[okg]
        c += np.bincount(idx, minlength=N * N)
        s1 += np.bincount(idx, weights=dg, minlength=N * N)
        np.maximum.at(zmax, idx, zg)
        np.minimum.at(zmin, idx, zg)
    return c, s1, zmax, zmin


print("window1 0-18s...")
c1, s1a, zmx1, zmn1 = grid_for(0, 18)
print("window2 35-70s...")
c2, s1b, zmx2, zmn2 = grid_for(35, 70)
m1 = np.where(c1 > 0, s1a / np.maximum(c1, 1), np.nan)
m2 = np.where(c2 > 0, s1b / np.maximum(c2, 1), np.nan)
diff = np.where((c1 >= 20) & (c2 >= 20), m2 - m1, np.nan)

ext = (-50, 50, -50, 50)
fig, axes = plt.subplots(1, 3, figsize=(19, 6.2))
im0 = axes[0].imshow(m1.reshape(N, N), origin="lower", extent=ext,
                     cmap="turbo", vmin=5, vmax=60)
axes[0].set_title("前 0~18s：平均测距")
axes[0].set_xlabel("x (m)")
axes[0].set_ylabel("y (m)")
fig.colorbar(im0, ax=axes[0], label="距离 (m)")
im1 = axes[1].imshow(m2.reshape(N, N), origin="lower", extent=ext,
                     cmap="turbo", vmin=5, vmax=60)
axes[1].set_title("后 35~70s：平均测距")
axes[1].set_xlabel("x (m)")
axes[1].set_ylabel("y (m)")
fig.colorbar(im1, ax=axes[1], label="距离 (m)")
im2 = axes[2].imshow(diff.reshape(N, N), origin="lower", extent=ext,
                     cmap="RdBu_r", vmin=-20, vmax=20)
axes[2].set_title("测距差值（后-前）\n红=变远 蓝=变近（同窗口样本≥20）")
axes[2].set_xlabel("x (m)")
axes[2].set_ylabel("y (m)")
fig.colorbar(im2, ax=axes[2], label="Δ距离 (m)")
# 标注主要变化区域
axes[2].plot(12, -7, "k*", ms=16, mec="w", mew=1.5)
axes[2].annotate("主要变化区\n82m→38m", (12, -7), textcoords="offset points",
                 xytext=(-70, 25), fontsize=10, color="black",
                 bbox=dict(boxstyle="round", fc="white", alpha=0.85))
fig.suptitle("NA70 抓包 2.pcap：移动前后场景对比（距离×0.01m，0.5m 栅格）",
             fontsize=14)
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(OUT, dpi=130)
print("saved", OUT)

# 变化量统计
chg = np.where(~np.isnan(diff) & (np.abs(diff) > 3))
print(f"变化栅格(>3m): {len(chg[0])}")
print(f"  |Δ|>5m: {np.sum(np.abs(diff[~np.isnan(diff)])>5)}")
print(f"  |Δ|>10m: {np.sum(np.abs(diff[~np.isnan(diff)])>10)}")
print(f"  Δ 中位: {np.nanmedian(diff):+.2f}m")
