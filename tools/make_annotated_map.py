#!/usr/bin/env python3
"""生成移动物体位置标注图：后窗口 BEV 密度 + 变化区域高亮 + 标注框。"""
import struct
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

PATH = r"D:\Code\radar\radar_capture_data\2.pcap"
OUT = r"D:\Code\radar\tools\analysis_output\fig_moving_object_annotated.png"
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
        xb = np.floor((x + 50) / S).astype(np.int64)
        yb = np.floor((y + 50) / S).astype(np.int64)
        okg = (xb >= 0) & (xb < N) & (yb >= 0) & (yb < N)
        idx = yb[okg] * N + xb[okg]
        c += np.bincount(idx, minlength=N * N)
        s1 += np.bincount(idx, weights=d[okg], minlength=N * N)
    return c, s1


print("window1 0-18s...")
c1, s1a = grid_for(0, 18)
print("window2 35-70s...")
c2, s1b = grid_for(35, 70)
m1 = np.where(c1 > 0, s1a / np.maximum(c1, 1), np.nan)
m2 = np.where(c2 > 0, s1b / np.maximum(c2, 1), np.nan)
diff = np.where((c1 >= 20) & (c2 >= 20), m2 - m1, np.nan)

ext = (-50, 50, -50, 50)
fig, ax = plt.subplots(figsize=(11, 9))

# 背景：后窗口点密度（灰度）
dens = np.log1p(c2.reshape(N, N))
ax.imshow(dens, origin="lower", extent=ext, cmap="gray_r", alpha=0.35)

# 变化区域高亮（|Δd|>5m 用红色半透明）
chg = np.where(np.abs(diff) > 5, 1, np.nan).reshape(N, N)
ax.imshow(chg, origin="lower", extent=ext, cmap="Reds", alpha=0.7, vmin=0, vmax=1)

# 主要变化区框
rect = Rectangle((2, -9), 14, 6, linewidth=3, edgecolor="red",
                 facecolor="none", linestyle="-")
ax.add_patch(rect)
ax.annotate("移动物体区域\n(20~30s 移入并停留)\n测距 82m → 38m",
            xy=(9, -6), xytext=(18, 5),
            fontsize=13, color="darkred", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="darkred", lw=2),
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="darkred", lw=1.5))

# 次要变化区
rect2 = Rectangle((-3, -8.5), 4, 3, linewidth=2, edgecolor="orange",
                  facecolor="none", linestyle="--")
ax.add_patch(rect2)
ax.annotate("次要变化区\n91m → 48m", xy=(-1, -7), xytext=(-30, -20),
            fontsize=10, color="darkorange",
            arrowprops=dict(arrowstyle="->", color="darkorange", lw=1.5),
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="darkorange"))

# 雷达位置
ax.plot(0, 0, "b^", ms=14, mec="white", mew=1.5)
ax.annotate("雷达\n(原点)", xy=(0, 0), xytext=(-48, 42),
            fontsize=10, color="blue",
            arrowprops=dict(arrowstyle="->", color="blue"))

ax.set_xlim(-50, 50)
ax.set_ylim(-50, 50)
ax.set_xlabel("x (m)")
ax.set_ylabel("y (m)")
ax.set_title("2.pcap 移动物体位置标注（后窗口 35~70s BEV 俯视图）\n"
             "红色区域 = 0~18s 与 35~70s 测距差异 >5m 的栅格",
             fontsize=13)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(OUT, dpi=140)
print("saved", OUT)
