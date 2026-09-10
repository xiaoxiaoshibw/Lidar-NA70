#!/usr/bin/env python3
"""NA70 移动目标判定 v5 —— 状态切换事件时空聚类（最终判定）。

1. 对每个 1°×1° 角度 bin 的距离-时间序列做分段（状态段，±2m 容差）；
2. 记录"状态切换事件"（同一 bin 主距离变化 >5m 的时刻）；
3. 事件按时间聚类（±30s）→ 移动事件；统计每个事件涉及的空间范围与距离变化；
4. 输出移动事件时空图与结论。

用法：python analyze_motion_v5.py <pcap>
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

MIN_N = 30           # bin 最少样本
STATE_TOL = 2.0      # 状态段距离容差(m)
CHANGE_MIN = 5.0     # 状态切换最小距离变化(m)
EVENT_WIN = 30.0     # 事件聚类时间窗(s)
SEG_MIN_T = 3.0      # 状态段最短持续(s)
SEG_MIN_N = 5        # 状态段最少样本数


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

    # 对每个 bin 分段并检测状态切换
    events = []  # (t, yaw, pitch, d_old, d_new)
    n_bin_ok = 0
    for bid in range(n_yaw * n_pitch):
        lo, hi = edges[bid], edges[bid + 1]
        n = hi - lo
        if n < MIN_N:
            continue
        n_bin_ok += 1
        dseg = Ds[lo:hi]
        tseg = Ts[lo:hi]
        o = np.argsort(tseg)
        tseg, dseg = tseg[o], dseg[o]
        # 分段：距离变化 > STATE_TOL 视为新段
        segs = []
        seg_start, seg_last = 0, 0
        for i in range(1, n):
            if abs(dseg[i] - dseg[seg_last]) > STATE_TOL:
                segs.append((tseg[seg_start], tseg[i - 1],
                             float(np.median(dseg[seg_start:i]))))
                seg_start = i
            seg_last = i
        segs.append((tseg[seg_start], tseg[-1],
                     float(np.median(dseg[seg_start:]))))
        # 过滤短段：持续 < SEG_MIN_T 或样本 < SEG_MIN_N 的段并入相邻段
        if len(segs) > 1:
            merged = []
            i = 0
            while i < len(segs):
                t0s, t1s, ds = segs[i]
                cnt = int(np.sum((tseg >= t0s) & (tseg <= t1s)))
                if (t1s - t0s) < SEG_MIN_T or cnt < SEG_MIN_N:
                    # 并入前段（若存在），否则并入后段
                    if merged:
                        pm = merged[-1]
                        merged[-1] = (pm[0], t1s,
                                      float(np.median([pm[2], ds])))
                    elif i + 1 < len(segs):
                        nx = segs[i + 1]
                        segs[i + 1] = (t0s, nx[1],
                                       float(np.median([ds, nx[2]])))
                    else:
                        merged.append(segs[i])
                else:
                    merged.append(segs[i])
                i += 1
            segs = merged
        # 相邻段主距离变化 > CHANGE_MIN → 事件
        for a, b_ in zip(segs[:-1], segs[1:]):
            if abs(b_[2] - a[2]) >= CHANGE_MIN and \
               (b_[0] - a[1]) < 60:  # 切换间隙不超过60s（避免拼接不相干段）
                y, p = divmod(bid, n_pitch)
                events.append(((a[1] + b_[0]) / 2, y, p, a[2], b_[2]))

    print(f"有效 bin {n_bin_ok}，状态切换事件 {len(events)}")

    # 事件时间聚类
    ev = sorted(events, key=lambda e: e[0])
    clusters = []
    for e in ev:
        if clusters and e[0] - clusters[-1][-1][0] <= EVENT_WIN:
            clusters[-1].append(e)
        else:
            clusters.append([e])
    clusters = [c for c in clusters if len(c) >= 3]
    print(f"时间聚类后的事件组（≥3个方向同时变）: {len(clusters)}")

    # 汇总事件组
    rows = []
    for i, c in enumerate(clusters):
        ts_c = [e[0] for e in c]
        ys = [e[1] for e in c]
        ps = [e[2] for e in c]
        dold = [e[3] for e in c]
        dnew = [e[4] for e in c]
        rows.append((i, min(ts_c), max(ts_c), len(c),
                     min(ys), max(ys), min(ps), max(ps),
                     float(np.median(dold)), float(np.median(dnew))))
        print(f"  事件组{i}: t=[{min(ts_c):.0f},{max(ts_c):.0f}]s "
              f"方向数={len(c)} yaw=[{min(ys)},{max(ys)}] "
              f"pitch=[{min(ps)},{max(ps)}] "
              f"距离 {np.median(dold):.0f}→{np.median(dnew):.0f}m")

    # ---------- 图：事件时空分布 ----------
    fig, axes = plt.subplots(2, 1, figsize=(14, 9))
    ax = axes[0]
    colors = plt.cm.turbo(np.linspace(0, 1, max(len(clusters), 1)))
    for (i, c), col in zip(enumerate(clusters), colors):
        ys = [e[1] for e in c]
        ps = [e[2] for e in c]
        ax.scatter(ys, ps, s=6, c=[col], label=f"事件组{i}(t={min(e[0] for e in c):.0f}s)" if i < 10 else None)
    ax.set_xlabel("yaw (°)")
    ax.set_ylabel("pitch (°)")
    ax.set_title(f"状态切换事件时空分布（{len(clusters)} 个事件组，每组≥3方向同时变化）")
    if len(clusters) <= 10:
        ax.legend(fontsize=8)
    ax = axes[1]
    for (i, c), col in zip(enumerate(clusters), colors):
        ts_c = [e[0] for e in c]
        ax.scatter(ts_c, [i] * len(ts_c), s=8, c=[col])
    ax.set_xlabel("抓包时间 (s)")
    ax.set_ylabel("事件组编号")
    ax.set_yticks(range(len(clusters)))
    ax.set_title("事件组时间分布")
    fig.tight_layout()
    p1 = OUTDIR / "fig_v5_events.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)

    # 最大事件组（人数最多的一组）的距离-时间展开
    if clusters:
        biggest = max(clusters, key=len)
        print(f"\n最大事件组: {len(biggest)} 方向, t=[{min(e[0] for e in biggest):.0f},{max(e[0] for e in biggest):.0f}]s")
        # 展开展示其中部分 bin 的完整距离时间序列
        binyaws = sorted(set(e[1] for e in biggest))
        show = binyaws[::max(1, len(binyaws) // 8)][:8]
        fig, axes = plt.subplots(len(show), 1, figsize=(13, 2.2 * len(show)), sharex=True)
        if len(show) == 1:
            axes = [axes]
        for ax, y in zip(axes, show):
            # 找该 yaw 附近事件涉及的 pitch
            pitches = sorted(set(e[2] for e in biggest if e[1] == y))
            for pi, p in enumerate(pitches[:2]):
                bid = y * n_pitch + p
                lo, hi = edges[bid], edges[bid + 1]
                if hi - lo == 0:
                    continue
                tseg, dseg = Ts[lo:hi], Ds[lo:hi]
                o = np.argsort(tseg)
                ax.plot(tseg[o], dseg[o], ".", ms=2.5, alpha=0.6,
                        label=f"pitch={p}°")
            ax.set_title(f"yaw={y}°")
            ax.set_ylabel("距离(m)")
            ax.legend(fontsize=7)
        axes[-1].set_xlabel("抓包时间 (s)")
        fig.tight_layout()
        p2 = OUTDIR / "fig_v5_biggest_event.png"
        fig.savefig(p2, dpi=130)
        plt.close(fig)
        print(f"图: {p1}\n    {p2}")

    # 结论
    print("\n================ 结论 ================")
    if len(clusters) >= 3:
        print(f"检测到 {len(clusters)} 组时空一致的测距状态切换事件（每组涉及 ≥3 个相邻方向")
        print(f"同时改变距离 {rows[0][8]:.0f}→{rows[0][9]:.0f}m 量级）")
        print("→ 数据中存在移动物体：物体移入/移出视场或移动后停留，"
              "造成大量方向的遮挡状态切换")
    elif len(clusters) >= 1:
        print(f"检测到 {len(clusters)} 组时空一致的测距状态切换事件")
        print("→ 存在移动物体迹象（证据中等）")
    else:
        print("→ 未检测到时空一致的移动事件（变化主要为静态遮挡/噪声）")


if __name__ == "__main__":
    sys.exit(main())
