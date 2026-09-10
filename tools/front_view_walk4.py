#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4.pcap 人的位置轮廓正视图（最终版）
- 使用已交付权威口径 walk4_summary.json 的逐帧遮挡带 (band_yaw_start/end)
- 主图: 从雷达视点看的角度空间正视图（墙背景 + 人扫过的阴影条 + 代表帧轮廓）
- 子图: 轮廓位置随时间移动（泳道）
输出: fig_walk4_front_view.png, walk4_front_view.json
"""
import json
import sys
from pathlib import Path

import numpy as np

YAW_RES = 0.0432
PITCH_RES = 0.01
OUT = Path(r"D:\Code\radar\tools\analysis_output")
YAW0, YAW1 = 140, 350


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Code\radar\radar_capture_data\4.pcap"
    import struct
    from pathlib import Path as _P

    def iter_udp(p):
        with open(p, "rb") as f:
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
        s = (tsb[0] & 0x7F) << 40
        for i in range(1, 6):
            s |= tsb[i] << (8 * (5 - i))
        return s + ((tsb[6] << 8) | tsb[7]) / 1000.0

    pkts = []
    for pl in iter_udp(path):
        if len(pl) < 1410:
            continue
        rb = np.frombuffer(pl[:1400], dtype=np.uint8).reshape(-1, 7)
        d = (rb[:, 0].astype(np.uint32) << 8) | rb[:, 1]
        p = (rb[:, 2].astype(np.uint32) << 8) | rb[:, 3]
        y = (rb[:, 4].astype(np.uint32) << 8) | rb[:, 5]
        rf = rb[:, 6]
        pkts.append((dev_ts(pl), d, p, y, rf))
    pkts.sort(key=lambda x: x[0])
    frames = []
    for p in pkts:
        if not frames or abs(p[0] - frames[-1][0][0]) > 0.25:
            frames.append([p])
        else:
            frames[-1].append(p)
    nf = len(frames)
    t0 = frames[0][0][0]
    tvec = np.array([g[0][0] - t0 for g in frames])

    mat = np.zeros((nf, 360), np.float32)
    for i, g in enumerate(frames):
        d = np.concatenate([q[1] for q in g])
        p = np.concatenate([q[2] for q in g])
        y = np.concatenate([q[3] for q in g])
        rf = np.concatenate([q[4] for q in g])
        m = (rf > 0) & (d >= 300) & (d < 1000) & (y < 60000) & (p < 60000)
        if not m.any():
            continue
        yd = np.clip(np.floor(y[m].astype(np.float64) * YAW_RES).astype(np.int32), 0, 359)
        np.add.at(mat, (i, yd), 1.0)

    quiet = list(range(0, 13)) + list(range(55, 92))
    base = np.median(mat[quiet], axis=0)
    rel1 = base >= 5.0

    # 墙体俯仰跨度（安静期）
    qpp = []
    for i in quiet:
        g = frames[i]
        d = np.concatenate([q[1] for q in g])
        p = np.concatenate([q[2] for q in g])
        y = np.concatenate([q[3] for q in g])
        rf = np.concatenate([q[4] for q in g])
        m = (rf > 0) & (d >= 300) & (d < 1000) & (y < 60000) & (p < 60000)
        qpp.append(p[m])
    qpp = np.concatenate(qpp)
    p_lo, p_hi = int(np.percentile(qpp, 1)), int(np.percentile(qpp, 99))
    print(f"墙体俯仰跨度: {p_lo}~{p_hi} 码 ({p_lo*PITCH_RES:.1f}~{p_hi*PITCH_RES:.1f}° @0.01; "
          f"{p_lo*0.0258:.1f}~{p_hi*0.0258:.1f}° @0.0258)")

    # 权威遮挡带（walk4_summary.json）
    summary = json.loads((OUT / "walk4_summary.json").read_text(encoding="utf-8"))
    bands = {}
    for idx, pf in enumerate(summary["per_frame"]):
        if pf["band_yaw_start"] is not None:
            bands[idx] = (pf["band_yaw_start"], pf["band_yaw_end"], pf["occ_center_yaw"])

    ev1 = list(range(13, 24))
    mid = list(range(24, 41))
    ev2 = list(range(41, 54))
    phcolor = {}
    for i in ev1:
        phcolor[i] = "#dc2626"
    for i in mid:
        phcolor[i] = "#f59e0b"
    for i in ev2:
        phcolor[i] = "#7c3aed"

    def phase_extent(idx):
        bs = [bands[k] for k in idx if k in bands]
        if not bs:
            return None
        ymin = min(b[0] for b in bs)
        ymax = max(b[1] for b in bs)
        widest = max(bs, key=lambda b: b[1] - b[0])
        return {"activity_yaw_range": [ymin, ymax],
                "activity_yaw_span_deg": ymax - ymin + 1,
                "dominant_band_yaw": [widest[0], widest[1]],
                "dominant_band_width_deg": widest[1] - widest[0] + 1,
                "pitch_range": [p_lo, p_hi],
                "pitch_span_deg": round((p_hi - p_lo) * PITCH_RES, 1)}

    e1 = phase_extent(ev1)
    em = phase_extent(mid)
    e2 = phase_extent(ev2)
    with open(OUT / "walk4_front_view.json", "w", encoding="utf-8") as f:
        json.dump({"event1": e1, "middle": em, "event2": e2,
                   "pitch_code_range": [p_lo, p_hi],
                   "note": "正视图=从雷达视点看的角空间遮挡足迹（遮挡带来自 walk4_summary 权威口径）；"
                           "竖向跨度=被遮挡的墙体俯仰跨度（人贴近雷达时盖满墙全高）",
                   "pitch_res_note": f"俯仰主假设 {PITCH_RES}°/码（若为 0.0258°/码，竖向跨度×2.58）"},
                  f, ensure_ascii=False, indent=1)
    print("事件1:", e1, "\n逗留期:", em, "\n事件2:", e2)

    # ---------------- 绘图 ----------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(13, 11))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.15, 1.0], hspace=0.16)
    ax = fig.add_subplot(gs[0])

    # 背景：安静期墙回波强度
    ax.bar(np.arange(360), np.log1p(base), width=1.0, color="#bae6fd", alpha=0.8, zorder=1)
    ax.axhspan(p_lo, p_hi, color="#93c5fd", alpha=0.10, zorder=0)

    # 人扫过的阴影条（逐帧遮挡带）
    for i in range(nf):
        if i not in bands or i == 93:
            continue
        y0, y1, c = bands[i]
        ax.add_patch(plt.Rectangle((y0 - 0.5, p_lo - 15), y1 - y0 + 1,
                                   (p_hi - p_lo) + 30, color=phcolor.get(i, "#9ca3af"),
                                   alpha=0.30, zorder=2, lw=0))

    # 代表帧轮廓（粗框）
    reps = [(16, "t=4.02s 走进深处\n208~251°", "#dc2626"),
            (32, "t=8.03s 逗留\n219~225°", "#f59e0b"),
            (50, "t=12.55s 折返深处\n290~318°", "#7c3aed")]
    for fi, lab, col in reps:
        if fi in bands:
            y0, y1, _ = bands[fi]
            ax.add_patch(plt.Rectangle((y0 - 0.5, p_lo - 15), y1 - y0 + 1,
                                       (p_hi - p_lo) + 30, fill=False, lw=2.8, ec=col, zorder=5))
            ax.annotate(lab, xy=((y0 + y1) / 2, p_hi + 20), xytext=((y0 + y1) / 2, p_hi + 115),
                        ha="center", fontsize=9.5, color=col, fontweight="bold", zorder=6,
                        arrowprops=dict(arrowstyle="->", color=col, lw=1.3))

    # 中心轨迹
    cys, cts = [], []
    for i in range(nf):
        if i in bands and 13 <= i < 54:
            cys.append(bands[i][2])
            cts.append(i)
    if len(cys) > 1:
        ax.plot(cys, [p_hi + 55] * len(cys), "-o", ms=5, lw=1.8, color="#111827",
                alpha=0.8, zorder=7, label="遮挡带中心 yaw（目标角位置）随时间移动")
        for j, i in enumerate(cts):
            if i in (14, 16, 44, 50):
                ax.annotate(f"{tvec[i]:.1f}s", (cys[j], p_hi + 55), fontsize=8,
                            color="#374151", ha="center", va="bottom")

    ax.set_xlim(YAW0, YAW1)
    ax.set_ylim(p_lo - 85, p_hi + 150)
    ax.set_xlabel("横向航向 yaw (°) —— 从雷达看过去的左右方向")
    ax.set_ylabel("俯仰 pitch (码值) —— 从雷达看过去的上下方向")
    ax.set_title("人的位置轮廓 · 正视图（从雷达视点看，角度空间遮挡足迹）")
    ax.grid(True, alpha=0.2)
    ax2 = ax.secondary_yaxis("right", functions=(lambda x: x * PITCH_RES,
                                                 lambda y: y / PITCH_RES))
    ax2.set_ylabel("俯仰 (°) ×0.01°/码")
    ax2.tick_params(labelsize=9)
    ax.legend(handles=[Patch(color="#dc2626", alpha=0.5, label="事件1 走进 3.3~5.8s"),
                       Patch(color="#f59e0b", alpha=0.5, label="逗留/慢移 6~10.3s"),
                       Patch(color="#7c3aed", alpha=0.5, label="事件2 折返 10.3~13.3s"),
                       plt.Line2D([0], [0], color="#111827", lw=1.8, label="中心轨迹")],
              loc="upper left", fontsize=9)

    # ---- 子图：泳道 ----
    axb = fig.add_subplot(gs[1])
    axb.set_title("遮挡轮廓（人的角位置）随时间移动 · 逐帧")
    for i in range(1, nf):
        if i not in bands or i == 93:
            continue
        y0, y1, _ = bands[i]
        axb.barh(y0, tvec[i] - tvec[i - 1], left=tvec[i - 1], height=y1 - y0 + 1,
                 color=phcolor.get(i, "#9ca3af"), alpha=0.8, edgecolor="none")
    axb.axvspan(3.3, 5.9, color="#dc2626", alpha=0.06)
    axb.axvspan(8.5, 13.4, color="#7c3aed", alpha=0.06)
    axb.set_xlabel("相对时间 (s)")
    axb.set_ylabel("遮挡带 yaw 范围 (°)")
    axb.set_xlim(0, float(tvec[-1]))
    axb.set_ylim(YAW0, YAW1)
    axb.grid(True, alpha=0.2)
    axb.legend(handles=[Patch(color="#dc2626", label="事件1 走进"),
                        Patch(color="#f59e0b", label="逗留/慢移"),
                        Patch(color="#7c3aed", label="事件2 折返")],
               loc="upper right", fontsize=9)

    fig.tight_layout()
    p1 = OUT / "fig_walk4_front_view.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    print("图:", p1)


if __name__ == "__main__":
    main()
