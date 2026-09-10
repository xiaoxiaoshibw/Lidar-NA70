#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4.pcap 移动目标（行人）检测：逐帧解析 -> 方向bin背景建模 -> 前景差分 -> 聚类 -> 轨迹。

输出到 tools/analysis_output/walk4_* :
  - fig_walk4_bev.png        前景点 BEV 俯视图（颜色=时间）
  - fig_walk4_timeline.png   每帧有效点数 + 前景点数 时间线
  - fig_walk4_traj_curves.png 距离/航向/俯仰 随帧变化曲线
  - walk4_trajectory.csv     逐帧目标质心轨迹
"""
import argparse
import struct
import sys
import time
from pathlib import Path

import numpy as np

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 0.01
OUTDIR = Path(r"D:\Code\radar\tools\analysis_output")

YAW_BIN, PITCH_BIN = 1.0, 1.0        # 方向 bin 大小（度）
RANGE_BIN = 0.5                       # 背景距离直方图 bin（米）
BG_MIN_FRAC = 0.05                    # 背景需出现在 >=5% 帧
FG_DEV_M = 1.5                        # 距背景偏差阈值（米）
D_MAX = 50.0                          # 有效最大距离
CODE_INVALID = 60000                  # 码值 >= 该值视为无效/噪声
CLUSTER_GAP_DEG = 2.0                 # 聚类角度间隙（度）
CLUSTER_MIN_PTS = 30


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
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", nargs="?", default=r"D:\Code\radar\radar_capture_data\4.pcap")
    args = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # ---- 解析并分帧 ----
    pkts = []
    for pl in iter_udp(args.pcap):
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
    print(f"包 {len(pkts)} -> 帧 {len(frames)}，耗时 {time.time()-t0:.0f}s")

    # 每帧点云
    fr = []  # (t, d, p, y, rf)
    for g in frames:
        t = g[0][0]
        d = np.concatenate([p[1] for p in g]).astype(np.float32) * DIST_RES_M
        p = np.concatenate([p[2] for p in g]).astype(np.float32) * PITCH_RES
        y = np.concatenate([p[3] for p in g]).astype(np.float32) * YAW_RES
        rf = np.concatenate([p[4] for p in g]).astype(np.uint8)
        fr.append((t, d, p, y, rf))
    T = np.array([f[0] for f in fr])

    # ---- 有效点过滤（去噪声/无效码） ----
    def valid_mask(f):
        d, p, y, rf = f[1], f[2], f[3], f[4]
        dc = (d / DIST_RES_M).astype(np.int64)
        pc = (p / PITCH_RES).astype(np.int64)
        yc = (y / YAW_RES).astype(np.int64)
        m = ((rf > 0) & (d >= 0.2) & (d <= D_MAX)
             & (dc < CODE_INVALID) & (pc < CODE_INVALID) & (yc < CODE_INVALID)
             & (y <= 360) & (p <= 360))
        return m

    counts = [int(valid_mask(f).sum()) for f in fr]
    print(f"每帧有效点: min {min(counts)}  max {max(counts)}  mean {np.mean(counts):.0f}")

    # ---- 背景建模：每个 (yaw,pitch) 1°bin 的稳定距离 ----
    nb_y, nb_p = int(360 / YAW_BIN), int(360 / PITCH_BIN)
    nf = len(fr)
    bg = np.full((nb_y, nb_p), np.nan)          # 背景距离
    bg_n = np.zeros((nb_y, nb_p), dtype=np.int32)  # 参与帧数
    hist_edges = np.arange(0.0, D_MAX + RANGE_BIN, RANGE_BIN)
    for f in fr:
        m = valid_mask(f)
        d, p, y = f[1][m], f[2][m], f[3][m]
        if not m.any():
            continue
        bi_y = np.clip(np.floor(y / YAW_BIN).astype(np.int32), 0, nb_y - 1)
        bi_p = np.clip(np.floor(p / PITCH_BIN).astype(np.int32), 0, nb_p - 1)
        key = bi_y * nb_p + bi_p
        uniq, inv = np.unique(key, return_inverse=True)
        for k in range(len(uniq)):
            sel = inv == k
            u = uniq[k]
            yy, pp = divmod(int(u), nb_p)
            dvals = d[sel]
            hist, _ = np.histogram(dvals, bins=hist_edges)
            if hist.max() == 0:
                continue
            peak = int(np.argmax(hist))
            bg[yy, pp] = (hist_edges[peak] + hist_edges[peak + 1]) / 2
            bg_n[yy, pp] += 1
    stable = bg_n >= max(2, BG_MIN_FRAC * nf)
    print(f"稳定背景 bin: {int(stable.sum())} / {nb_y*nb_p}")

    # ---- 前景差分：距背景偏差 > 阈值 或 方向无背景却有强回波 ----
    fg_t, fg_x, fg_y, fg_z, fg_d, fg_ya, fg_pi = [], [], [], [], [], [], []
    per_frame_fg = []
    for i, f in enumerate(fr):
        m = valid_mask(f)
        d, p, y, rf = f[1][m], f[2][m], f[3][m], f[4][m]
        if not m.any():
            per_frame_fg.append(0)
            continue
        bi_y = np.clip(np.floor(y / YAW_BIN).astype(np.int32), 0, nb_y - 1)
        bi_p = np.clip(np.floor(p / PITCH_BIN).astype(np.int32), 0, nb_p - 1)
        bgd = bg[bi_y, bi_p]
        is_stable = stable[bi_y, bi_p]
        dev = np.abs(d - bgd)
        # 前景：稳定方向偏离远，或 非稳定方向且强回波(可能是新目标)
        fg = ((is_stable & (dev > FG_DEV_M)) |
              (~is_stable & (rf >= 60) & (d <= 8.0)))
        per_frame_fg.append(int(fg.sum()))
        if not fg.any():
            continue
        d_f, p_f, y_f, rf_f = d[fg], p[fg], y[fg], rf[fg]
        th = y_f * np.pi / 180
        ph = p_f * np.pi / 180
        fg_t.append(np.full(len(d_f), T[i], dtype=np.float32))
        fg_x.append(d_f * np.cos(th))
        fg_y.append(d_f * np.sin(th))
        fg_z.append(d_f * np.sin(ph))
        fg_d.append(d_f)
        fg_ya.append(y_f)
        fg_pi.append(p_f)
    FT = np.concatenate(fg_t)
    FX = np.concatenate(fg_x)
    FY = np.concatenate(fg_y)
    FZ = np.concatenate(fg_z)
    FD = np.concatenate(fg_d)
    FYA = np.concatenate(fg_ya)
    FPI = np.concatenate(fg_pi)
    print(f"前景点总数 {len(FT)}，耗时 {time.time()-t0:.0f}s")

    # ---- 逐帧聚类 -> 目标质心轨迹 ----
    rows = []
    for i, f in enumerate(fr):
        m = valid_mask(f)
        d, p, y, rf = f[1][m], f[2][m], f[3][m], f[4][m]
        if not m.any():
            continue
        bi_y = np.clip(np.floor(y / YAW_BIN).astype(np.int32), 0, nb_y - 1)
        bi_p = np.clip(np.floor(p / PITCH_BIN).astype(np.int32), 0, nb_p - 1)
        bgd = bg[bi_y, bi_p]
        is_stable = stable[bi_y, bi_p]
        dev = np.abs(d - bgd)
        fg = ((is_stable & (dev > FG_DEV_M)) |
              (~is_stable & (rf >= 60) & (d <= 8.0)))
        if fg.sum() < CLUSTER_MIN_PTS:
            continue
        # 在 (yaw, pitch) 空间做简易邻域聚类：按 yaw 排序，间隙>2° 断开，再按 pitch 分
        y_f, p_f, d_f, rf_f = y[fg], p[fg], d[fg], rf[fg]
        o = np.argsort(y_f)
        y_s, p_s, d_s = y_f[o], p_f[o], d_f[o]
        cuts = np.where(np.diff(y_s) > CLUSTER_GAP_DEG)[0]
        segs = np.split(np.arange(len(y_s)), cuts + 1)
        best = None
        for seg in segs:
            if len(seg) < CLUSTER_MIN_PTS:
                continue
            # 簇内再按 pitch 分离（若 pitch 跨度过大则取主块）
            ps = p_s[seg]
            d_med = np.median(d_s[seg])
            okp = np.abs(ps - np.median(ps)) <= 15
            if okp.sum() < CLUSTER_MIN_PTS:
                okp = np.ones(len(seg), dtype=bool)
            seg2 = seg[okp]
            n = len(seg2)
            if best is None or n > best[0]:
                best = (n, y_s[seg2], p_s[seg2], d_s[seg2], rf[fg][o][seg2])
        if best is None:
            continue
        n, ys, ps, ds, rfs = best
        rows.append((T[i], n, float(np.median(ys)), float(np.median(ps)),
                     float(np.median(ds)), float(rfs.max())))
    rows = np.array(rows, dtype=float) if rows else np.zeros((0, 6))
    print(f"含目标帧数 {len(rows)}")

    np.save(OUTDIR / "walk4_fg_pts.npy",
            np.column_stack([FT, FX, FY, FZ, FD, FYA, FPI]))
    np.save(OUTDIR / "walk4_traj.npy", rows)
    np.save(OUTDIR / "walk4_perframe_fg.npy",
            np.array(per_frame_fg, dtype=np.int32))
    np.save(OUTDIR / "walk4_counts.npy", np.array(counts, dtype=np.int32))

    # ---- 图1：BEV 前景点（颜色=时间）----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    tmin, tmax = FT.min(), FT.max()
    fig, ax = plt.subplots(figsize=(9, 9))
    sc = ax.scatter(FX, FY, c=FT, s=1.2, cmap="plasma", vmin=tmin, vmax=tmax, alpha=0.55)
    ax.plot(0, 0, "b^", ms=14, mec="white")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title(f"4.pcap 前景点 BEV（{len(FT):,} 点，颜色=相对时间）")
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal")
    fig.colorbar(sc, ax=ax, label="相对时间 (s)")
    fig.tight_layout()
    p1 = OUTDIR / "fig_walk4_bev.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)

    # ---- 图2：时间线 ----
    fig, ax = plt.subplots(figsize=(13, 4.5))
    ax.plot(T - T[0], counts, "-", lw=1.2, label="每帧有效点数", color="steelblue")
    ax2 = ax.twinx()
    ax2.plot(T - T[0], per_frame_fg, "-", lw=1.2, label="每帧前景点数", color="crimson")
    ax.set_xlabel("相对时间 (s)")
    ax.set_ylabel("有效点数", color="steelblue")
    ax2.set_ylabel("前景点数", color="crimson")
    ax.set_title("4.pcap 每帧点数时间线（前景=移动目标信号）")
    fig.tight_layout()
    p2 = OUTDIR / "fig_walk4_timeline.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)

    # ---- 图3：目标轨迹曲线 ----
    if len(rows) >= 2:
        fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True)
        t = rows[:, 0] - T[0]
        axes[0].plot(t, rows[:, 4], "-o", ms=2.5, color="darkorange")
        axes[0].set_ylabel("距离 (m)")
        axes[0].grid(True, alpha=0.3)
        axes[0].set_title("逐帧目标质心：距离 / 航向 / 俯仰")
        axes[1].plot(t, rows[:, 2], "-o", ms=2.5, color="seagreen")
        axes[1].set_ylabel("航向 yaw (°)")
        axes[1].grid(True, alpha=0.3)
        axes[2].plot(t, rows[:, 3], "-o", ms=2.5, color="purple")
        axes[2].set_ylabel("俯仰 pitch (°)")
        axes[2].set_xlabel("相对时间 (s)")
        axes[2].grid(True, alpha=0.3)
        fig.tight_layout()
        p3 = OUTDIR / "fig_walk4_traj_curves.png"
        fig.savefig(p3, dpi=130)
        plt.close(fig)

        # CSV
        hdr = "t_rel_s,frame_count,median_yaw_deg,median_pitch_deg,median_range_m,max_refl\n"
        with open(OUTDIR / "walk4_trajectory.csv", "w", newline="") as fp:
            fp.write(hdr)
            for r in rows:
                fp.write(f"{r[0]-T[0]:.3f},{int(r[1]):d},{r[2]:.2f},{r[3]:.2f},{r[4]:.2f},{int(r[5]):d}\n")
        # 速度估计
        v = np.diff(rows[:, 4]) / np.diff(rows[:, 0])
        v = np.abs(v[v < 8.0])
        print(f"径向速度: 中位 {np.median(v):.2f} m/s, p95 {np.percentile(v,95):.2f} m/s")
        print(f"目标距离范围: {rows[:,4].min():.2f} ~ {rows[:,4].max():.2f} m")
        print(f"目标航向范围: {rows[:,2].min():.1f} ~ {rows[:,2].max():.1f} °")
        print(f"图: {p1}\n    {p2}\n    {p3}")
        print(f"CSV: {OUTDIR / 'walk4_trajectory.csv'}")
    else:
        print("未提取到足够的目标轨迹点")


if __name__ == "__main__":
    sys.exit(main())
