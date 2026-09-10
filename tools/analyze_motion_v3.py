#!/usr/bin/env python3
"""NA70 移动目标判定 v3 —— 前景点时空追踪。

方法：
  1. 每个 (yaw,pitch) 角度 bin 建立"背景距离"（距离直方图峰值）；
  2. 偏离背景距离 > 阈值的点 = 前景点（移动物体/遮挡物）；
  3. 前景点按时间分块、按角度邻接聚类 → 事件实例；
  4. 相邻时间块的事件按最近邻关联 → 目标轨迹；
  5. 轨迹持续多帧且位置连续移动 → 确凿的移动物体证据。

用法：python analyze_motion_v3.py <pcap>
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
DIST_RES_M = 16.0 / 1000.0
OUTDIR = Path(r"D:\Code\radar\tools\analysis_output")

FG_GAP_M = 3.0        # 前景偏离背景阈值
CHUNK_S = 1.0         # 时间分块（秒）
TRACK_MAX_GAP_BIN = 6  # 事件关联的角度 bin 距离门限
MIN_TRACK_CHUNKS = 4   # 轨迹最少持续块数
MIN_EVENT_PTS = 4      # 事件最少点数


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
    # 收集 (t, bin_id, d)
    all_t, all_b, all_d = [], [], []
    n_pkt = 0
    for ts, payload in iter_udp_payloads(args.pcap):
        if len(payload) < 1410:
            continue
        n_pkt += 1
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
        ok = (refl > 0) & (d >= 0.5) & (d <= 120)
        if not ok.any():
            continue
        bin_id = np.clip(
            np.floor(yaw[ok] / 1.0).astype(np.int32) * n_pitch
            + np.floor(pitch[ok] / 1.0).astype(np.int32),
            0, n_yaw * n_pitch - 1)
        all_t.append(np.full(int(ok.sum()), ts, dtype=np.float32))
        all_b.append(bin_id)
        all_d.append(d[ok])
    T = np.concatenate(all_t)
    B = np.concatenate(all_b)
    D = np.concatenate(all_d)
    del all_t, all_b, all_d
    print(f"点总数 {len(T)}，处理 {n_pkt} 包，耗时 {time.time()-t0:.0f}s")

    # 每 bin 背景距离 = 距离直方图峰值（0.2m 分箱）
    order = np.argsort(B, kind="stable")
    Bs, Ds, Ts = B[order], D[order], T[order]
    del B, D, T
    edges = np.searchsorted(Bs, np.arange(n_yaw * n_pitch + 1))
    counts = np.diff(edges)

    bg_dist = np.full(n_yaw * n_pitch, np.nan, dtype=np.float32)
    for bid in range(n_yaw * n_pitch):
        lo, hi = edges[bid], edges[bid + 1]
        if hi - lo < 5:
            continue
        dseg = Ds[lo:hi]
        # 0.2m 分箱取峰值
        h, be = np.histogram(dseg, bins=np.arange(0, 121, 0.2))
        bg_dist[bid] = float(be[np.argmax(h)])
    n_bg = int(np.isfinite(bg_dist).sum())
    print(f"有背景的 bin: {n_bg}")

    # 前景点：与背景距离差 > 阈值
    bg_sel = bg_dist[Bs]
    fg_mask = np.abs(Ds - bg_sel) > FG_GAP_M
    n_fg = int(fg_mask.sum())
    print(f"前景点（偏离背景>{FG_GAP_M}m）: {n_fg} "
          f"({100*n_fg/max(len(Ds),1):.3f}%)")
    if n_fg == 0:
        print("无前景点 → 结论：未检测到移动物体")
        return

    FBs, FDs, FTs = Bs[fg_mask], Ds[fg_mask], Ts[fg_mask]
    # 前景点时间分块（1s）
    chunk = np.floor(FTs / CHUNK_S).astype(np.int64)
    # 每块内：按 bin 邻接聚类事件
    events = []  # (chunk, [bin_ids], 点数)
    for c in range(int(chunk.min()), int(chunk.max()) + 1):
        m = chunk == c
        if m.sum() < MIN_EVENT_PTS:
            continue
        cb = FBs[m]
        ub, inv = np.unique(cb, return_inverse=True)
        if len(ub) < 2:
            events.append((c, ub.tolist(), int(m.sum())))
            continue
        # bin 邻接（yaw 环接）：构造图，BFS 连通
        yb = ub // n_pitch
        pb = ub % n_pitch
        pos = {int(u): i for i, u in enumerate(ub)}
        nbr = {i: [] for i in range(len(ub))}
        for i, (y, p) in enumerate(zip(yb, pb)):
            for dy, dp in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny = (y + dy) % n_yaw
                nb = pos.get(ny * n_pitch + (p + dp))
                if nb is not None and nb != i:
                    nbr[i].append(nb)
        seen = set()
        for i in range(len(ub)):
            if i in seen:
                continue
            stack = [i]
            comp = []
            while stack:
                j = stack.pop()
                if j in seen:
                    continue
                seen.add(j)
                comp.append(j)
                stack.extend(nbr[j])
            if len(comp) >= 1:
                events.append((c, [int(ub[j]) for j in comp], int(m.sum())))

    print(f"事件实例（1s 块 × 角度连通）: {len(events)}")
    # 事件中心
    ev = []  # (c, cyaw_bin, cpitch_bin, d_mean, npts)
    for c, bins, npts in events:
        yb = np.mean([b // n_pitch for b in bins])
        pb = np.mean([b % n_pitch for b in bins])
        ev.append((c, yb, pb, npts))
    ev = sorted(ev, key=lambda x: x[0])
    if not ev:
        print("无事件 → 结论：未检测到移动物体")
        return

    # 轨迹关联：相邻块事件最近邻（角度 bin 距离门限）
    tracks = []  # list of (chunks, cyaw list, cpitch list)
    for c, yb, pb, npts in ev:
        if not tracks:
            tracks.append([(c, yb, pb)])
            continue
        # 找上一块内的最近轨迹
        best_tr, best_d = None, 1e9
        for ti, tr in enumerate(tracks):
            if tr[-1][0] != c - 1:
                continue
            dy = min(abs(tr[-1][1] - yb), 360 - abs(tr[-1][1] - yb))
            dd = abs(tr[-1][2] - pb)
            dist = dy + dd
            if dist < best_d:
                best_d = dist
                best_tr = ti
        if best_tr is not None and best_d <= TRACK_MAX_GAP_BIN:
            tracks[best_tr].append((c, yb, pb))
        else:
            tracks.append([(c, yb, pb)])

    long_tracks = [tr for tr in tracks if len(tr) >= MIN_TRACK_CHUNKS]
    print(f"轨迹（事件关联）: {len(tracks)}，持续>={MIN_TRACK_CHUNKS}s 的: {len(long_tracks)}")

    # 判定"移动"：轨迹跨越多块且角度位置变化大（或持续存在）
    moving = []
    for tr in long_tracks:
        ys = [t[1] for t in tr]
        ps = [t[2] for t in tr]
        dur = tr[-1][0] - tr[0][0]
        y_span = max(ys) - min(ys)
        p_span = max(ps) - min(ps)
        moving.append((tr, dur, y_span, p_span))

    print("\n== 长轨迹明细 ==")
    for i, (tr, dur, y_span, p_span) in enumerate(moving[:15]):
        print(f"轨迹{i}: 持续{dur}s 角度跨度(yaw {y_span:.0f}°, pitch {p_span:.0f}°) "
              f"起点(yaw={tr[0][1]:.0f},pitch={tr[0][2]:.0f}) "
              f"终点(yaw={tr[-1][1]:.0f},pitch={tr[-1][2]:.0f})")

    # 画图：前景事件时间-角度
    fig, ax = plt.subplots(figsize=(13, 6))
    ec = np.array([(e[0], e[1], e[2]) for e in ev])
    sc = ax.scatter(ec[:, 0] * CHUNK_S, ec[:, 1], s=8, c=ec[:, 2], cmap="jet", alpha=0.7)
    ax.set_xlabel("时间 (s)")
    ax.set_ylabel("yaw bin (°)")
    ax.set_title(f"前景事件（时间 vs 角度方向），颜色=pitch；连续斜线=移动目标轨迹")
    fig.colorbar(sc, ax=ax, label="pitch (°)")
    fig.tight_layout()
    p_out = OUTDIR / "fig_v3_foreground_tracks.png"
    fig.savefig(p_out, dpi=130)
    plt.close(fig)

    has_moving = len(moving) > 0
    print(f"\n图已保存: {p_out}")
    print("\n================ 结论 ================")
    if has_moving:
        big = [m for m in moving if m[2] >= 5 or m[3] >= 3]
        print(f"检测到 {len(moving)} 条持续前景轨迹；其中角度位置显著移动的 {len(big)} 条")
        print("→ 存在移动物体" if big else "→ 前景轨迹角度几乎不动，倾向静态遮挡/静止目标，移动证据弱")
    else:
        print("未检测到持续前景轨迹 → 无明显移动物体")


if __name__ == "__main__":
    sys.exit(main())
