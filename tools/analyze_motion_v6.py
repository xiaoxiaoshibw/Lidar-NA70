#!/usr/bin/env python3
"""NA70 移动目标判定 v6 —— 方向-时间切换轨迹（黄金证据图）。

对每个角度 bin 找到"主距离值首次切换时刻"，画 t_change vs (yaw, pitch)。
移动物体穿过视场 → 相邻方向依次被遮挡/暴露 → t_change 在角度空间呈连续带；
若 t_change 随机散布 → 独立局部噪声/静态遮挡。

用法：python analyze_motion_v6.py <pcap>
"""
import argparse
import struct
import sys
import time
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 0.01
OUTDIR = Path(r"D:\Code\radar\tools\analysis_output")

MIN_N = 40
# 距离量化到 2m，找两个主簇（各占>=15%采样），簇中心差>=6m 视为有切换
Q = 2.0
PCT = 0.15
DSEP = 6.0


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
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", nargs="?", default=r"D:\Code\radar\radar_capture_data\2.pcap")
    args = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    n_yaw, n_pitch = 360, 90
    Tb, Bb, Db = [], [], []
    for ts, payload in iter_udp_payloads(args.pcap):
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
        yaw = ycode.astype(np.float32) * YAW_RES
        pitch = pcode.astype(np.float32) * PITCH_RES
        ok = (refl > 0) & (d >= 1.0) & (d <= 100)
        if not ok.any():
            continue
        bin_id = np.clip(
            np.floor(yaw[ok] / 1.0).astype(np.int32) * n_pitch
            + np.floor(pitch[ok] / 1.0).astype(np.int32), 0, n_yaw * n_pitch - 1)
        Tb.append(np.full(int(ok.sum()), ts, dtype=np.float32))
        Bb.append(bin_id)
        Db.append(d[ok])
    T = np.concatenate(Tb)
    B = np.concatenate(Bb)
    D = np.concatenate(Db)
    del Tb, Bb, Db
    print(f"点 {len(T)}，耗时 {time.time()-t0:.0f}s")

    order = np.argsort(B, kind="stable")
    Bs, Ds, Ts = B[order], D[order], T[order]
    del B, D, T
    edges = np.searchsorted(Bs, np.arange(n_yaw * n_pitch + 1))

    switches = []  # (t_change, yaw, pitch, d1, d2)
    for bid in range(n_yaw * n_pitch):
        lo, hi = edges[bid], edges[bid + 1]
        n = hi - lo
        if n < MIN_N:
            continue
        dseg = Ds[lo:hi]
        tseg = Ts[lo:hi]
        # 距离量化直方图 → 主簇
        dq = np.floor(dseg / Q).astype(np.int64)
        vals, counts = np.unique(dq, return_counts=True)
        frac = counts / n
        big = np.argsort(counts)[::-1]
        d1q = vals[big[0]]
        d1 = (d1q + 0.5) * Q
        if counts[big[0]] < PCT * n:
            continue
        # 找与 d1 分隔 >= DSEP 的第二大簇
        d2 = None
        for k in big[1:]:
            if counts[k] >= PCT * n and abs(vals[k] - d1q) * Q >= DSEP:
                d2 = (vals[k] + 0.5) * Q
                break
        if d2 is None:
            continue
        # 首次切换时刻：按时间排序，找 d 从簇1变到簇2
        o = np.argsort(tseg)
        tsort, dsort = tseg[o], dseg[o]
        cur = 1 if abs(dsort[0] - d1) < abs(dsort[0] - d2) else 2
        tc = None
        for tt, dd in zip(tsort, dsort):
            new = 1 if abs(dd - d1) < abs(dd - d2) else 2
            if new != cur:
                tc = tt
                break
        if tc is not None:
            y, p = divmod(bid, n_pitch)
            switches.append((tc, y, p, d1, d2))

    switches.sort()
    print(f"检测到双主簇切换的 bin: {len(switches)}")

    # ---------- 图1：t_change vs yaw（颜色=pitch） ----------
    if switches:
        sw = np.array(switches, dtype=float)
        fig, ax = plt.subplots(figsize=(15, 6))
        sc = ax.scatter(sw[:, 1], sw[:, 0], c=sw[:, 2], s=5,
                        cmap="viridis", vmin=0, vmax=90)
        ax.set_xlabel("yaw (°)")
        ax.set_ylabel("首次状态切换时刻 (s)")
        ax.set_title(f"状态切换时刻 vs 方向（{len(switches)} 个 bin；连续带=移动物体穿越轨迹）")
        fig.colorbar(sc, ax=ax, label="pitch (°)")
        fig.tight_layout()
        p1 = OUTDIR / "fig_v6_switch_time.png"
        fig.savefig(p1, dpi=130)
        plt.close(fig)

        # 切换时刻直方图
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.hist(sw[:, 0], bins=np.arange(0, 156, 3), color="steelblue")
        ax.set_xlabel("切换时刻 (s)")
        ax.set_ylabel("bin 数量")
        ax.set_title("状态切换时刻分布（尖峰=一次移动事件）")
        fig.tight_layout()
        p2 = OUTDIR / "fig_v6_switch_hist.png"
        fig.savefig(p2, dpi=130)
        plt.close(fig)

        # ---------- 图2：切换时刻在 yaw-pitch 空间的分布 ----------
        fig, ax = plt.subplots(figsize=(14, 6))
        # 按切换时刻分桶着色（每 10s 一档）
        tb = np.floor(sw[:, 0] / 10).astype(int) * 10
        cmap = plt.cm.turbo
        norm = plt.Normalize(0, 150)
        for tbin in sorted(set(tb)):
            m = tb == tbin
            ax.scatter(sw[m, 1], sw[m, 2], s=6,
                       c=cmap(norm(tbin)), label=f"{tbin}-{tbin+10}s" if tbin % 30 == 0 else None)
        ax.set_xlabel("yaw (°)")
        ax.set_ylabel("pitch (°)")
        ax.set_title("状态切换时刻在角度空间的分布（颜色=切换时刻）")
        if len(set(tb)) <= 16:
            ax.legend(fontsize=8, ncol=4)
        fig.tight_layout()
        p3 = OUTDIR / "fig_v6_switch_angle.png"
        fig.savefig(p3, dpi=130)
        plt.close(fig)

        print(f"图: {p1}\n    {p2}\n    {p3}")
        # 打印切换时刻最集中的时段
        hist, be = np.histogram(sw[:, 0], bins=np.arange(0, 156, 5))
        top = np.argsort(hist)[::-1][:6]
        print("\n切换最集中的时段:")
        for k in sorted(top):
            if hist[k] > 0:
                print(f"  {be[k]:.0f}-{be[k]+5:.0f}s: {hist[k]} 个 bin 切换")
    else:
        print("未检测到显著状态切换")


if __name__ == "__main__":
    sys.exit(main())
