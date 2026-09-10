#!/usr/bin/env python3
"""NA70 固定雷达移动目标检测 v2 —— 角度 bin 距离-时间分析。

不依赖扫描周期/帧对齐（双楔镜利萨茹扫描图案不闭合），而是直接分析：
  每个 (yaw, pitch) 角度 bin 的测距序列。静态方向测距稳定；
  移动物体经过的方向会出现距离大幅变化（物体接近/离开/遮挡切换）。

流程：
  1. 流式解析全部包 → 点 (yaw_deg, pitch_deg, dist_m, refl, t)
  2. 按 1°×1° 角度 bin 分组
  3. 每 bin：样本数、距离范围/分位差 → 候选移动 bin
  4. 候选 bin 空间连通聚类 → 移动区域
  5. 输出区域距离-时间曲线与可视化

用法：python analyze_motion_v2.py <pcap>
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
DIST_RES_M = 16.0 / 1000.0
OUTDIR = Path(r"D:\Code\radar\tools\analysis_output")

# 判定参数
BIN_YAW_DEG = 1.0
BIN_PITCH_DEG = 1.0
MIN_SAMPLES = 15          # bin 最少被扫到次数
MIN_DIST_RANGE_M = 3.0    # 距离变化幅值阈值
MIN_REL_CHANGE = 0.15     # 相对变化阈值
DIST_LO, DIST_HI = 0.5, 120.0


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

    n_yaw = int(360 / BIN_YAW_DEG)
    n_pitch = int(90 / BIN_PITCH_DEG)

    all_t, all_bin, all_d = [], [], []
    n_pkt = 0
    n_empty = 0
    n_valid = 0
    for ts, payload in iter_udp_payloads(args.pcap):
        if len(payload) < 1410:
            continue
        n_pkt += 1
        b = np.frombuffer(payload[:1400], dtype=np.uint8).reshape(-1, 7)
        dcode = (b[:, 0].astype(np.uint32) << 8) | b[:, 1]
        pcode = (b[:, 2].astype(np.uint32) << 8) | b[:, 3]
        ycode = (b[:, 4].astype(np.uint32) << 8) | b[:, 5]
        refl = b[:, 6]
        if not (dcode.any() or pcode.any() or ycode.any()):
            n_empty += 1
            continue
        d = dcode.astype(np.float64) * DIST_RES_M
        yaw = ycode.astype(np.float64) * YAW_RES
        pitch = pcode.astype(np.float64) * PITCH_RES
        ok = (refl > 0) & (d >= DIST_LO) & (d <= DIST_HI) & np.isfinite(d)
        if not ok.any():
            continue
        bin_id = np.floor(yaw / BIN_YAW_DEG).astype(np.int32) * n_pitch + \
                 np.floor(pitch / BIN_PITCH_DEG).astype(np.int32)
        bin_id = np.clip(bin_id, 0, n_yaw * n_pitch - 1)
        all_t.append(np.full(int(ok.sum()), ts, dtype=np.float32))
        all_bin.append(bin_id[ok].astype(np.int32))
        all_d.append(d[ok].astype(np.float32))
        n_valid += int(ok.sum())

    T = np.concatenate(all_t)
    B = np.concatenate(all_bin)
    D = np.concatenate(all_d)
    del all_t, all_bin, all_d
    print(f"包数 {n_pkt}（空包 {n_empty}），有效点 {n_valid}，耗时 {time.time()-t0:.0f}s")
    print(f"时长 {T[-1]:.1f}s，平均每包有效点 {n_valid/n_pkt:.0f}")

    # 按 bin 分组
    order = np.argsort(B, kind="stable")
    Bs, Ds, Ts = B[order], D[order], T[order]
    del B, D, T
    edges = np.searchsorted(Bs, np.arange(n_yaw * n_pitch + 1))
    counts = np.diff(edges)

    # 每 bin 统计（只对有样本的 bin）
    cand = []  # (bin_id, n, dmin, dmax, p90, p10, dmean)
    for bid in range(n_yaw * n_pitch):
        lo, hi = edges[bid], edges[bid + 1]
        n = hi - lo
        if n < MIN_SAMPLES:
            continue
        dseg = Ds[lo:hi]
        dmin, dmax = float(dseg.min()), float(dseg.max())
        dmean = float(dseg.mean())
        rng = dmax - dmin
        if rng >= MIN_DIST_RANGE_M and rng / max(dmean, 1e-3) >= MIN_REL_CHANGE:
            p10, p90 = float(np.percentile(dseg, 10)), float(np.percentile(dseg, 90))
            cand.append((bid, int(n), dmin, dmax, p90, p10, dmean))

    print(f"候选移动 bin（{MIN_SAMPLES}样本, 距离变化>={MIN_DIST_RANGE_M}m, "
          f"相对>={MIN_REL_CHANGE}）: {len(cand)}")
    if not cand:
        print("未检测到候选移动 bin → 结论：无移动物体")
        return

    # 候选 bin 空间连通聚类（yaw×pitch 邻接）
    cand_map = {bid: i for i, (bid, *_r) in enumerate(cand)}
    adj = deque()
    for i, (bid, *_) in enumerate(cand):
        y, p = divmod(bid, n_pitch)
        for dy, dp in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nb = ((y + dy) % n_yaw) * n_pitch + (p + dp)
            if nb in cand_map and cand_map[nb] != i:
                adj.append((i, cand_map[nb]))
    parent = list(range(len(cand)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in adj:
        union(a, b)
    groups = {}
    for i in range(len(cand)):
        groups.setdefault(find(i), []).append(i)

    regions = []
    for gid, members in groups.items():
        if len(members) < 2:  # 单 bin 孤点不算区域
            continue
        bins = [cand[m][0] for m in members]
        ys = [b // n_pitch for b in bins]
        ps = [b % n_pitch for b in bins]
        total_n = sum(cand[m][1] for m in members)
        regions.append({
            "members": members,
            "n_bins": len(members),
            "n": total_n,
            "yaw_deg": float(np.mean(ys)),
            "pitch_deg": float(np.mean(ps)),
            "d_range": max(cand[m][4] for m in members) - min(cand[m][5] for m in members),
            "d_mean": float(np.mean([cand[m][6] for m in members])),
        })
    regions.sort(key=lambda r: -r["d_range"])
    print(f"连通区域（>=2 bin）: {len(regions)}")

    # 每个区域：取最活跃 bin 输出距离-时间曲线
    print("\n== 移动区域明细 ==")
    region_repr = []
    for i, rg in enumerate(regions[:12]):
        # 区域代表 bin：距离范围最大的
        best = max(rg["members"], key=lambda m: cand[m][4] - cand[m][5])
        bid = cand[best][0]
        lo, hi = edges[bid], edges[bid + 1]
        ts_seg = Ts[lo:hi]
        d_seg = Ds[lo:hi]
        o = np.argsort(ts_seg)
        ts_seg, d_seg = ts_seg[o], d_seg[o]
        region_repr.append((i, rg, bid, ts_seg, d_seg))
        print(f"区域{i}: bin数={rg['n_bins']} 样本={rg['n']} "
              f"中心(yaw={rg['yaw_deg']:.1f}°, pitch={rg['pitch_deg']:.1f}°) "
              f"距离范围≈{rg['d_range']:.1f}m 平均距离≈{rg['d_mean']:.0f}m")

    # ---------- 图1：区域距离-时间曲线 ----------
    fig, axes = plt.subplots(min(6, len(region_repr)), 1, figsize=(12, 2.2 * min(6, len(region_repr))),
                             sharex=True)
    if len(region_repr) == 1:
        axes = [axes]
    for ax, (i, rg, bid, ts_seg, d_seg) in zip(axes, region_repr[:6]):
        ax.plot(ts_seg, d_seg, ".", ms=3, alpha=0.6)
        ax.set_ylabel(f"区域{i}\n距离(m)")
        ax.set_title(f"区域{i}: 角度方向约 (yaw={rg['yaw_deg']:.0f}°, pitch={rg['pitch_deg']:.0f}°) "
                     f"距离 {rg['d_range']:.0f}m 范围")
    axes[-1].set_xlabel("抓包时间 (s)")
    fig.suptitle("移动候选区域的测距随时间变化（距离跳变=物体进入/离开/移动）", y=1.0)
    fig.tight_layout()
    p1 = OUTDIR / "fig_v2_dist_time_curves.png"
    fig.savefig(p1, dpi=130, bbox_inches="tight")
    plt.close(fig)

    # ---------- 图2：全点 BEV 密度 + 移动区域位置 ----------
    # 重新抽样点做 BEV（避免全部点太密）
    stride = max(1, n_valid // 2000000)
    fig, ax = plt.subplots(figsize=(10, 8))
    b2 = np.floor(Bs[::stride] / n_pitch).astype(np.float64) * BIN_YAW_DEG
    p2 = (Bs[::stride] % n_pitch).astype(np.float64) * BIN_PITCH_DEG
    d2 = Ds[::stride]
    # 用距离着色：近距离亮
    sc = ax.scatter(b2, p2, c=d2, s=0.3, cmap="viridis_r", alpha=0.35)
    ax.set_xlabel("yaw (°)")
    ax.set_ylabel("pitch (°)")
    ax.set_title("全部有效点（yaw-pitch 图，颜色=距离），方框=移动区域")
    for rg in regions[:12]:
        w = min(12, rg["n_bins"] * 3)
        ax.add_patch(plt.Rectangle((rg['yaw_deg'] - w / 2, rg['pitch_deg'] - 3),
                                   w, 6, fill=False, edgecolor="red", lw=1.5))
    fig.colorbar(sc, ax=ax, label="距离 (m)")
    fig.tight_layout()
    p2f = OUTDIR / "fig_v2_motion_regions.png"
    fig.savefig(p2f, dpi=130)
    plt.close(fig)

    print(f"\n图已保存: {p1}")
    print(f"          {p2f}")
    print(f"\n结论：检测到 {len(regions)} 个角度方向上存在显著测距变化 → "
          f"{'存在移动物体' if regions else '无明显移动物体'}")


if __name__ == "__main__":
    sys.exit(main())
