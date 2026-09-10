#!/usr/bin/env python3
"""NA70 移动目标最终分析（距离分辨率 0.01m 修正版）。

  1. BEV 栅格(0.5m)测距标准差 → 动态区域识别；
  2. 动态栅格空间连通聚类 → 动态目标簇；
  3. 每簇距离-时间曲线 → 移动模式验证；
  4. 输出结论与可视化图。

用法：python analyze_motion_final.py <pcap>
"""
import argparse
import struct
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 0.01          # 10mm，由 z 分布验证（地面峰 12-14m）
OUTDIR = Path(r"D:\Code\radar\tools\analysis_output")
GRID_S = 0.5
GRID_N = 320               # ±80m
STD_MOVING = 4.0           # 动态栅格阈值
MIN_GRID_CNT = 30          # 栅格最少样本


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

    # 收集点（x, y, d）与每栅格的时间-距离明细（仅高样本栅格）
    xs, ys, ds = [], [], []
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
        pitch = pcode.astype(np.float32) * PITCH_RES * np.pi / 180
        yaw = ycode.astype(np.float32) * YAW_RES * np.pi / 180
        ok = (refl > 0) & (d >= 1.0) & (d <= 100)
        if not ok.any():
            continue
        xs.append((d * np.cos(yaw) * np.cos(pitch))[ok])
        ys.append((d * np.sin(yaw) * np.cos(pitch))[ok])
        ds.append(d[ok])
    X = np.concatenate(xs)
    Y = np.concatenate(ys)
    D = np.concatenate(ds)
    del xs, ys, ds
    print(f"有效点 {len(X)}，耗时 {time.time()-t0:.0f}s")

    # 栅格统计
    N = GRID_N
    S = GRID_S
    xb = np.floor((X + 80) / S).astype(np.int64)
    yb = np.floor((Y + 80) / S).astype(np.int64)
    okg = (xb >= 0) & (xb < N) & (yb >= 0) & (yb < N)
    xb, yb, Dg = xb[okg], yb[okg], D[okg]
    idx = yb * N + xb
    cnt = np.bincount(idx, minlength=N * N).astype(np.float64)
    s1 = np.bincount(idx, weights=Dg, minlength=N * N)
    s2 = np.bincount(idx, weights=Dg * Dg, minlength=N * N)
    mean = np.where(cnt > 0, s1 / np.maximum(cnt, 1), 0)
    var = np.maximum(np.where(cnt > 1, s2 / np.maximum(cnt, 1) - mean * mean, 0), 0)
    std = np.sqrt(var)

    dyn = (cnt >= MIN_GRID_CNT) & (std > STD_MOVING)
    print(f"占用栅格 {(cnt>0).sum()}，动态栅格(std>{STD_MOVING}m, n>={MIN_GRID_CNT}) {dyn.sum()}")

    # 动态栅格空间连通聚类（4-邻接）
    dyn_ids = np.where(dyn)[0]
    dyn_set = set(map(int, dyn_ids))
    clusters = []
    seen = set()
    for g in dyn_ids:
        if int(g) in seen:
            continue
        stack = [int(g)]
        comp = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            y, x = divmod(cur, N)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < N and 0 <= nx < N:
                    nid = ny * N + nx
                    if nid in dyn_set and nid not in seen:
                        stack.append(nid)
        if comp:
            clusters.append(comp)
    clusters.sort(key=len, reverse=True)
    print(f"动态区域（连通簇）: {len(clusters)}")
    for i, comp in enumerate(clusters[:12]):
        gx = np.mean([c % N for c in comp]) * S - 80
        gy = np.mean([c // N for c in comp]) * S - 80
        gstd = np.mean([std[c] for c in comp])
        gcnt = int(sum(cnt[c] for c in comp))
        print(f"  簇{i}: {len(comp)}栅格 中心({gx:.1f},{gy:.1f}) "
              f"平均std={gstd:.1f}m 样本={gcnt}")

    # ---------- 图1：方差热力图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
    dens = np.zeros(N * N)
    dens[idx] += 1
    im0 = axes[0].imshow(np.log1p(dens.reshape(N, N)), origin="lower",
                         extent=(-80, 80, -80, 80), cmap="gray_r")
    axes[0].set_title("点密度 (log)   [距离×0.01m]")
    axes[0].set_xlabel("x (m)")
    axes[0].set_ylabel("y (m)")
    im1 = axes[1].imshow(std.reshape(N, N), origin="lower",
                         extent=(-80, 80, -80, 80), cmap="hot", vmax=10)
    axes[1].set_title(f"测距标准差（红色=动态区域，阈值>{STD_MOVING}m）")
    axes[1].set_xlabel("x (m)")
    axes[1].set_ylabel("y (m)")
    fig.colorbar(im1, ax=axes[1], label="距离 std (m)")
    fig.tight_layout()
    p1 = OUTDIR / "fig_final_grid_var.png"
    fig.savefig(p1, dpi=120)
    plt.close(fig)

    # ---------- 图2：动态区域的时间-距离曲线 ----------
    # 对每个动态簇抽样点做距离-时间曲线（需要重新遍历或缓存：用栅格成员点）
    # 简化：对每个簇选 std 最大的栅格，用其全部点画 d-t
    fig, axes = plt.subplots(min(4, len(clusters)), 1,
                             figsize=(12, 2.6 * min(4, len(clusters))), sharex=True)
    if len(clusters) == 1:
        axes = [axes]
    cluster_curves = []
    for i, comp in enumerate(clusters[:4]):
        best = max(comp, key=lambda c: std[c])
        m = idx == best
        ts_pts = np.full(int(m.sum()), np.nan)  # 占位，改用收集时的 t
        # 需要 t —— 从已收集数组重算：重新遍历代价大，改为第二遍收集
        cluster_curves.append(best)
        gx = (best % N) * S - 80 + S / 2
        gy = (best // N) * S - 80 + S / 2
        axes[i].set_title(f"簇{i} 代表栅格({gx:.1f},{gy:.1f}) 距离-时间（第二遍补采）")
        axes[i].set_ylabel("距离(m)")
    if clusters:
        axes[-1].set_xlabel("抓包时间 (s)")
    fig.tight_layout()
    p2 = OUTDIR / "fig_final_cluster_curves.png"
    fig.savefig(p2, dpi=120)
    plt.close(fig)
    # 第二遍：收集代表栅格的距离-时间
    targets = cluster_curves[:4]
    if targets:
        tlist = [[] for _ in targets]
        dlist = [[] for _ in targets]
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
            pitch = pcode.astype(np.float32) * PITCH_RES * np.pi / 180
            yaw = ycode.astype(np.float32) * YAW_RES * np.pi / 180
            ok = (refl > 0) & (d >= 1.0) & (d <= 100)
            if not ok.any():
                continue
            x = d * np.cos(yaw) * np.cos(pitch)
            y = d * np.sin(yaw) * np.cos(pitch)
            xbi = np.floor((x + 80) / S).astype(np.int64)
            ybi = np.floor((y + 80) / S).astype(np.int64)
            valid = (xbi >= 0) & (xbi < N) & (ybi >= 0) & (ybi < N)
            idi = np.where(valid, ybi * N + xbi, -1)
            for ti, tg in enumerate(targets):
                m = idi == tg
                if m.any():
                    tlist[ti].append(np.full(int(m.sum()), ts))
                    dlist[ti].append(d[m])
        fig, axes = plt.subplots(len(targets), 1,
                                 figsize=(12, 2.6 * len(targets)), sharex=True)
        if len(targets) == 1:
            axes = [axes]
        for ti, (tg, ax) in enumerate(zip(targets, axes)):
            if tlist[ti]:
                Tt = np.concatenate(tlist[ti])
                Dt = np.concatenate(dlist[ti])
                o = np.argsort(Tt)
                ax.plot(Tt[o], Dt[o], ".", ms=2.5, alpha=0.5)
                gx = (tg % N) * S - 80 + S / 2
                gy = (tg // N) * S - 80 + S / 2
                ax.set_title(f"动态簇{ti} 栅格中心({gx:.1f},{gy:.1f})："
                             f"距离 {Dt.min():.0f}~{Dt.max():.0f}m（大幅波动=移动/遮挡）")
                ax.set_ylabel("距离(m)")
        axes[-1].set_xlabel("抓包时间 (s)")
        fig.tight_layout()
        p2 = OUTDIR / "fig_final_cluster_curves.png"
        fig.savefig(p2, dpi=120)
        plt.close(fig)

    # 结论
    print(f"\n图1: {p1}")
    print(f"图2: {p2}")
    print("\n================ 结论 ================")
    if len(clusters) >= 1:
        total = sum(len(c) for c in clusters)
        print(f"检测到 {len(clusters)} 个动态区域（{total} 个栅格，"
              f"测距标准差 > {STD_MOVING}m，样本充足）")
        print("→ 数据中存在移动物体/动态目标")
    else:
        print("未检测到动态区域 → 无明显移动物体")


if __name__ == "__main__":
    sys.exit(main())
