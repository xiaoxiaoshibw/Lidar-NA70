#!/usr/bin/env python3
"""提取并单独描绘移动点：角度 bin 背景建模 + 前景差分。

1. 用 0~18s（移动事件前）建立每个角度 bin 的背景距离（直方图峰值）；
2. 全量数据中，距离偏离背景 >5m 的点判为移动/前景点；
3. 单独画移动点的 BEV 图（按时间着色）和 3D 散点图。
"""
import struct
import sys
import time
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 0.01
OUTDIR = Path(r"D:\Code\radar\tools\analysis_output")
PATH = r"D:\Code\radar\radar_capture_data\2.pcap"

N_YAW, N_PITCH = 360, 90
BG_T_END = 18.0      # 背景窗口 0~18s
FG_THRESH = 5.0      # 前景偏离阈值(m)


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


def main():
    t0 = time.time()
    # ---- 第一遍：建立背景（0~18s）----
    print("第一遍：建立背景模型（0~18s）...")
    bg_s1 = np.zeros(N_YAW * N_PITCH)
    bg_s2 = np.zeros(N_YAW * N_PITCH)
    bg_c = np.zeros(N_YAW * N_PITCH)
    # 用距离直方图峰值更稳健：收集每个 bin 的距离列表（采样）
    bg_dlists = [[] for _ in range(N_YAW * N_PITCH)]
    for ts, payload in iter_udp_payloads(PATH):
        if len(payload) < 1410 or ts > BG_T_END:
            continue
        rb = np.frombuffer(payload[:1400], dtype=np.uint8).reshape(-1, 7)
        dcode = (rb[:, 0].astype(np.uint32) << 8) | rb[:, 1]
        pcode = (rb[:, 2].astype(np.uint32) << 8) | rb[:, 3]
        ycode = (rb[:, 4].astype(np.uint32) << 8) | rb[:, 5]
        refl = rb[:, 6]
        if not (dcode.any() or pcode.any() or ycode.any()):
            continue
        d = dcode.astype(np.float32) * DIST_RES_M
        yaw = ycode.astype(np.float32) * YAW_RES
        pitch = pcode.astype(np.float32) * PITCH_RES
        ok = (refl > 0) & (d >= 1.0) & (d <= 100)
        if not ok.any():
            continue
        bid = np.clip(
            np.floor(yaw[ok] / 1.0).astype(np.int32) * N_PITCH
            + np.floor(pitch[ok] / 1.0).astype(np.int32), 0, N_YAW * N_PITCH - 1)
        for b, dd in zip(bid, d[ok]):
            bg_dlists[int(b)].append(float(dd))
    bg_d = np.full(N_YAW * N_PITCH, np.nan)
    bg_n = np.zeros(N_YAW * N_PITCH, dtype=int)
    for i in range(N_YAW * N_PITCH):
        if len(bg_dlists[i]) >= 10:
            arr = np.array(bg_dlists[i])
            # 距离直方图峰值（2m bin）
            hist, edges = np.histogram(arr, bins=np.arange(0, 102, 2))
            peak = np.argmax(hist)
            bg_d[i] = (edges[peak] + edges[peak + 1]) / 2
            bg_n[i] = len(arr)
    del bg_dlists
    print(f"  背景 bin 数: {np.sum(bg_n >= 10)}，耗时 {time.time()-t0:.0f}s")

    # ---- 第二遍：提取移动点 ----
    print("第二遍：提取移动点（全量）...")
    mv_t, mv_x, mv_y, mv_z, mv_d = [], [], [], [], []
    total_pts = 0
    for ts, payload in iter_udp_payloads(PATH):
        if len(payload) < 1410:
            continue
        rb = np.frombuffer(payload[:1400], dtype=np.uint8).reshape(-1, 7)
        dcode = (rb[:, 0].astype(np.uint32) << 8) | rb[:, 1]
        pcode = (rb[:, 2].astype(np.uint32) << 8) | rb[:, 3]
        ycode = (rb[:, 4].astype(np.uint32) << 8) | rb[:, 5]
        refl = rb[:, 6]
        if not (dcode.any() or pcode.any() or ycode.any()):
            continue
        d = dcode.astype(np.float32) * DIST_RES_M
        pitch = pcode.astype(np.float32) * PITCH_RES * np.pi / 180
        yaw = ycode.astype(np.float32) * YAW_RES * np.pi / 180
        ok = (refl > 0) & (d >= 1.0) & (d <= 100)
        if not ok.any():
            continue
        bid = np.clip(
            np.floor(ycode[ok].astype(np.float32) * YAW_RES / 1.0).astype(np.int32) * N_PITCH
            + np.floor(pcode[ok].astype(np.float32) * PITCH_RES / 1.0).astype(np.int32),
            0, N_YAW * N_PITCH - 1)
        total_pts += int(ok.sum())
        bg = bg_d[bid]
        valid_bg = ~np.isnan(bg)
        if not valid_bg.any():
            continue
        dev = np.abs(d[ok][valid_bg] - bg[valid_bg])
        fg = dev > FG_THRESH
        if not fg.any():
            continue
        idx = np.where(valid_bg)[0][fg]
        d_fg = d[ok][idx]
        pitch_fg = pitch[ok][idx]
        yaw_fg = yaw[ok][idx]
        mv_t.append(np.full(len(idx), ts, dtype=np.float32))
        mv_x.append(d_fg * np.cos(yaw_fg) * np.cos(pitch_fg))
        mv_y.append(d_fg * np.sin(yaw_fg) * np.cos(pitch_fg))
        mv_z.append(d_fg * np.sin(pitch_fg))
        mv_d.append(d_fg)
    MT = np.concatenate(mv_t)
    MX = np.concatenate(mv_x)
    MY = np.concatenate(mv_y)
    MZ = np.concatenate(mv_z)
    MD = np.concatenate(mv_d)
    print(f"  总点 {total_pts}，移动点 {len(MT)}（{len(MT)/total_pts*100:.1f}%），耗时 {time.time()-t0:.0f}s")

    # ---- 图1：移动点 BEV（按时间着色）----
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    sc = axes[0].scatter(MX, MY, c=MT, s=1.5, cmap="plasma",
                         vmin=0, vmax=153, alpha=0.6)
    axes[0].set_xlim(-50, 50)
    axes[0].set_ylim(-50, 50)
    axes[0].set_xlabel("x (m)")
    axes[0].set_ylabel("y (m)")
    axes[0].set_title(f"移动点 BEV 俯视图（共 {len(MT):,} 点，颜色=时间）")
    axes[0].grid(True, alpha=0.3)
    axes[0].plot(0, 0, "b^", ms=12, mec="white")
    fig.colorbar(sc, ax=axes[0], label="抓包时间 (s)")

    # ---- 图2：移动点按时间段分色 ----
    periods = [(0, 18, "前 0-18s", "gray"),
               (18, 35, "移动中 18-35s", "red"),
               (35, 70, "后 35-70s", "blue"),
               (70, 153, "末 70-153s", "green")]
    for t0p, t1p, label, col in periods:
        m = (MT >= t0p) & (MT < t1p)
        if m.any():
            axes[1].scatter(MX[m], MY[m], c=col, s=1.5, alpha=0.5,
                            label=f"{label} ({m.sum():,}点)")
    axes[1].set_xlim(-50, 50)
    axes[1].set_ylim(-50, 50)
    axes[1].set_xlabel("x (m)")
    axes[1].set_ylabel("y (m)")
    axes[1].set_title("移动点按时间段分色")
    axes[1].grid(True, alpha=0.3)
    axes[1].plot(0, 0, "b^", ms=12, mec="white")
    axes[1].legend(fontsize=9, markerscale=5)
    fig.tight_layout()
    p1 = OUTDIR / "fig_moving_points_bev.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    print(f"图1: {p1}")

    # ---- 图3：移动点 3D 散点 ----
    fig = plt.figure(figsize=(13, 9))
    ax = fig.add_subplot(111, projection="3d")
    # 采样避免过密
    if len(MT) > 80000:
        idx = np.random.choice(len(MT), 80000, replace=False)
    else:
        idx = np.arange(len(MT))
    sc = ax.scatter(MX[idx], MY[idx], MZ[idx], c=MT[idx], s=1.2,
                    cmap="plasma", vmin=0, vmax=153, alpha=0.5)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_zlabel("z 深度 (m)")
    ax.set_title(f"移动点 3D 分布（采样 {len(idx):,} 点，颜色=时间）")
    fig.colorbar(sc, ax=ax, label="时间 (s)", shrink=0.6)
    p2 = OUTDIR / "fig_moving_points_3d.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    print(f"图2: {p2}")

    # ---- 统计 ----
    print("\n移动点统计:")
    print(f"  时间段分布:")
    for t0p, t1p, label, _ in periods:
        m = (MT >= t0p) & (MT < t1p)
        print(f"    {label}: {m.sum():,} 点 ({m.sum()/len(MT)*100:.1f}%)")
    print(f"  位置: x[{MX.min():.0f},{MX.max():.0f}] y[{MY.min():.0f},{MY.max():.0f}] z[{MZ.min():.0f},{MZ.max():.0f}]")
    print(f"  距离: [{MD.min():.1f}, {MD.max():.1f}] m, 中位 {np.median(MD):.1f}m")


if __name__ == "__main__":
    sys.exit(main())
