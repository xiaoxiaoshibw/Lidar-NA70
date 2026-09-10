#!/usr/bin/env python3
"""NA70 移动目标判定 v4 —— 角度 bin 变化模式分类（最终判定）。

对每个 1°×1° (yaw,pitch) 角度 bin 的测距-时间序列分类：
  - STATIC   : 距离稳定（静态背景）
  - OCCLUSION: 距离在 2~3 个稳定值间切换（静止前后景遮挡，非移动）
  - MOVING   : 距离连续大范围变化（移动物体通过/停留）

MOVING bin 的数量与空间聚集性决定结论。输出分类图与证据。

用法：python analyze_motion_v4.py <pcap>
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

MIN_N = 20            # bin 最少样本
N_UNIQ_OCC = 3        # 遮挡类唯一距离值上限（1m 量化）
MED_DD_MOVING = 0.5   # 连续变化判据：相邻样本 |Δd| 中位数（m）
RANGE_MOVING = 6.0    # MOVING 类距离范围下限


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

    n_static = n_occ = n_moving = 0
    mov_bins = []
    occ_bins = []
    for bid in range(n_yaw * n_pitch):
        lo, hi = edges[bid], edges[bid + 1]
        n = hi - lo
        if n < MIN_N:
            continue
        dseg = Ds[lo:hi]
        tseg = Ts[lo:hi]
        rng = float(dseg.max() - dseg.min())
        if rng < 2.0:
            n_static += 1
            continue
        # 距离量化到 1m 的唯一值数
        n_uniq = len(np.unique(np.floor(dseg)))
        o = np.argsort(tseg)
        dd = np.abs(np.diff(dseg[o]))
        med_dd = float(np.median(dd)) if len(dd) else 0.0
        if n_uniq <= N_UNIQ_OCC and med_dd < MED_DD_MOVING:
            n_occ += 1
            occ_bins.append(bid)
        elif rng >= RANGE_MOVING and med_dd >= MED_DD_MOVING:
            n_moving += 1
            mov_bins.append(bid)
        elif rng >= RANGE_MOVING:
            # 变化大但相邻变化小：需要看模式，归为"疑似"
            n_occ += 1
            occ_bins.append(bid)
        else:
            n_static += 1

    total_cls = n_static + n_occ + n_moving
    print(f"\n角度 bin 分类（样本>={MIN_N}）:")
    print(f"  STATIC  (距离稳定)          : {n_static}")
    print(f"  OCCLUSION(遮挡双值切换)     : {n_occ}")
    print(f"  MOVING  (连续大范围变化)    : {n_moving}   <- 移动物体证据")

    # MOVING bin 空间分布图
    fig, ax = plt.subplots(figsize=(12, 5.5))
    grid = np.zeros((n_pitch, n_yaw), dtype=np.int8)
    for bid in mov_bins:
        y, p = divmod(bid, n_pitch)
        grid[p, y] = 2
    for bid in occ_bins:
        y, p = divmod(bid, n_pitch)
        if grid[p, y] == 0:
            grid[p, y] = 1
    im = ax.imshow(grid, origin="lower", aspect="auto", extent=(0, 360, 0, 90),
                   cmap="RdYlBu_r", vmin=0, vmax=2)
    ax.set_xlabel("yaw (°)")
    ax.set_ylabel("pitch (°)")
    ax.set_title(f"角度域变化模式分类：蓝=遮挡/间歇变化({n_occ})，红=连续移动({n_moving})")
    cbar = fig.colorbar(im, ax=ax, ticks=[0, 1, 2])
    cbar.ax.set_yticklabels(["STATIC", "OCCLUSION", "MOVING"])
    fig.tight_layout()
    p1 = OUTDIR / "fig_v4_class_map.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)

    # MOVING bin 聚类（角度邻接）
    if mov_bins:
        mset = set(mov_bins)
        clusters = []
        seen = set()
        for b in mov_bins:
            if b in seen:
                continue
            stack = [b]
            comp = []
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                comp.append(cur)
                y, p = divmod(cur, n_pitch)
                for dy, dp in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nb = ((y + dy) % n_yaw) * n_pitch + (p + dp)
                    if nb in mset and nb not in seen:
                        stack.append(nb)
            clusters.append(comp)
        clusters.sort(key=len, reverse=True)
        print(f"\nMOVING bin 连通区域: {len(clusters)}")
        for i, comp in enumerate(clusters[:10]):
            ys = [c // n_pitch for c in comp]
            ps = [c % n_pitch for c in comp]
            print(f"  区域{i}: {len(comp)} bin, 中心(yaw={np.mean(ys):.0f}°, "
                  f"pitch={np.mean(ps):.0f}°), yaw范围[{min(ys)},{max(ys)}] "
                  f"pitch范围[{min(ps)},{max(ps)}]")

        # MOVING 区域代表 bin 的距离-时间曲线
        repr_bins = [clusters[i][np.argmax([1 for _ in clusters[i]])] for i in range(min(6, len(clusters)))]
        fig, axes = plt.subplots(len(repr_bins), 1, figsize=(12, 2.4 * len(repr_bins)), sharex=True)
        if len(repr_bins) == 1:
            axes = [axes]
        for ax, bid in zip(axes, repr_bins):
            lo, hi = edges[bid], edges[bid + 1]
            tseg, dseg = Ts[lo:hi], Ds[lo:hi]
            o = np.argsort(tseg)
            ax.plot(tseg[o], dseg[o], ".", ms=3, alpha=0.6)
            y, p = divmod(bid, n_pitch)
            ax.set_title(f"MOVING bin (yaw={y}°, pitch={p}°)：距离连续变化 {dseg.min():.0f}~{dseg.max():.0f}m")
            ax.set_ylabel("距离(m)")
        axes[-1].set_xlabel("抓包时间 (s)")
        fig.tight_layout()
        p2 = OUTDIR / "fig_v4_moving_curves.png"
        fig.savefig(p2, dpi=130)
        plt.close(fig)
        print(f"\n图: {p1}")
        print(f"    {p2}")

    print("\n================ 结论 ================")
    if n_moving >= 8:
        print(f"检测到 {n_moving} 个角度方向存在连续大范围测距变化（聚类为 {len(clusters)} 个空间区域）")
        print("→ 数据中存在移动物体（测距随时间连续变化 = 目标在移动）")
    elif n_occ > 0 and n_moving >= 2:
        print(f"移动方向较少({n_moving})，但遮挡类变化方向较多({n_occ})")
        print("→ 存在间歇性进入视场的动态目标（遮挡事件），移动物体证据中等")
    else:
        print("→ 未发现明显移动物体（测距变化以静态遮挡/噪声为主）")


if __name__ == "__main__":
    sys.exit(main())
