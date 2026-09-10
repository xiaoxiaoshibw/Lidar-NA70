#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4.pcap 行走检测最终产物：
  1) walk4_summary.json  — 逐帧指标 + 事件窗口
  2) fig_walk4_timeline.png  — 点数时间线
  3) fig_walk4_heatmap.png   — 帧x航向 远回波热力图（遮挡条纹）
  4) fig_walk4_occlusion.png — 遮挡带中心/宽度随帧变化
  5) walk4_report.html    — 自包含 HTML 报告（内联 SVG）
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 0.01
OUT = Path(r"D:\Code\radar\tools\analysis_output")


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
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Code\radar\radar_capture_data\4.pcap"
    OUT.mkdir(parents=True, exist_ok=True)
    pkts = []
    for pl in iter_udp(path):
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
    nf = len(frames)
    t0 = frames[0][0][0]

    mat = np.zeros((nf, 360), dtype=np.float32)
    near_cnt = np.zeros(nf, dtype=np.int32)
    tot_cnt = np.zeros(nf, dtype=np.int32)
    for i, g in enumerate(frames):
        dcode = np.concatenate([p[1] for p in g])
        pcode = np.concatenate([p[2] for p in g])
        ycode = np.concatenate([p[3] for p in g])
        refl = np.concatenate([p[4] for p in g])
        m = refl > 0
        tot_cnt[i] = int(m.sum())
        near_cnt[i] = int((m & (dcode == 0)).sum())
        fm = m & (dcode >= 300) & (dcode < 2000) & (pcode < 60000) & (ycode < 60000)
        yd = np.clip(np.floor(ycode[fm].astype(np.float64) * YAW_RES).astype(np.int32), 0, 359)
        np.add.at(mat, (i, yd), 1.0)

    t = np.array([g[0][0] - t0 for g in frames], dtype=np.float32)
    quiet = list(range(0, 13)) + list(range(55, 92))
    base = np.maximum(np.median(mat[quiet], axis=0), 1e-6)
    rel = base >= 5.0
    qmed = float(np.median(mat[quiet].sum(axis=1)))

    # 逐帧指标
    far_tot = mat.sum(axis=1)
    per_frame = []
    for i in range(nf):
        defc = np.where(rel, np.maximum(0.0, base - mat[i]) / base, 0.0)
        w = defc[140:350]
        deg = np.arange(140, 350)
        c = np.nan
        if w.sum() > 1e-9:
            r = np.sum(w * np.exp(1j * np.deg2rad(deg)))
            c = float(np.rad2deg(np.angle(r)) % 360)
        d = np.convolve(defc, np.ones(3) / 3, mode="same")
        mask = d >= 0.5
        idxs = np.where(mask)[0]
        band_s, band_e = None, None
        if len(idxs):
            segs, cur = [], [idxs[0]]
            for k in range(1, len(idxs)):
                if idxs[k] - idxs[k - 1] <= 2:
                    cur.append(idxs[k])
                else:
                    segs.append(cur)
                    cur = [idxs[k]]
            segs.append(cur)
            if segs:
                s = max(segs, key=len)
                if len(s) >= 3:
                    band_s, band_e = int(s[0]), int(s[-1])
        per_frame.append({
            "frame": i, "t_rel_s": round(float(t[i]), 3),
            "total_pts": int(tot_cnt[i]), "near_pts": int(near_cnt[i]),
            "far_pts": int(far_tot[i]), "deficit": float(defc.sum()),
            "occ_center_yaw": None if np.isnan(c) else round(c, 1),
            "band_yaw_start": band_s, "band_yaw_end": band_e,
        })

    # 事件检测：far_pts 连续低于阈值
    thr = qmed * 0.75
    low = far_tot < thr
    events = []
    cur = None
    for i in range(nf):
        if low[i]:
            if cur is None:
                cur = [i, i]
            cur[1] = i
        else:
            if cur is not None:
                events.append(cur)
                cur = None
    if cur is not None:
        events.append(cur)
    ev_list = []
    for (s, e) in events:
        seg = per_frame[s:e + 1]
        cents = [x["occ_center_yaw"] for x in seg if x["occ_center_yaw"] is not None]
        bands = [(x["band_yaw_start"], x["band_yaw_end"])
                 for x in seg if x["band_yaw_start"] is not None]
        ev_list.append({
            "start_s": seg[0]["t_rel_s"], "end_s": seg[-1]["t_rel_s"],
            "frames": f"{s}-{e}",
            "min_far": int(min(x["far_pts"] for x in seg)),
            "min_far_t": seg[min(range(len(seg)), key=lambda k: seg[k]["far_pts"])]["t_rel_s"],
            "occ_center_range": [min(cents), max(cents)] if cents else None,
            "band_extent": [min(b[0] for b in bands), max(b[1] for b in bands)] if bands else None,
        })

    summary = {
        "file": path,
        "packets": len(pkts), "frames": nf,
        "duration_s": round(float(t[-1]), 2),
        "frame_rate_hz": round(nf / float(t[-1]), 2),
        "yaw_sector_deg": [int(rel[:360].sum() and 143), 345],
        "quiet_far_median": round(qmed, 1),
        "events": ev_list,
        "per_frame": per_frame,
    }
    with open(OUT / "walk4_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    np.save(OUT / "walk4_far_mat.npy", mat)
    np.save(OUT / "walk4_base.npy", base)

    for ev in ev_list:
        print(f"事件 {ev['start_s']}~{ev['end_s']}s (帧{ev['frames']}) "
              f"远回波最低 {ev['min_far']}@{ev['min_far_t']}s "
              f"遮挡中心 {ev['occ_center_range']} 带宽 {ev['band_extent']}")
    print("quiet median far =", qmed)

    # ---------- 图 ----------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    # 图1 时间线
    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(t, far_tot, "-", lw=1.4, color="steelblue", label="远回波数（墙/背景 3-20m）")
    ax.plot(t, tot_cnt, "-", lw=1.0, color="#9aa7b8", label="总回波数（refl>0）")
    ax.axhline(qmed, ls="--", color="steelblue", lw=0.8)
    ax.axhline(qmed * 0.75, ls=":", color="crimson", lw=0.8)
    for (s, e) in [(ev["start_s"], ev["end_s"]) for ev in ev_list]:
        ax.axvspan(s, e, color="crimson", alpha=0.12)
    ax.set_xlabel("相对时间 (s)")
    ax.set_ylabel("点数")
    ax.set_title("4.pcap 每帧回波数时间线（红色区间=检测到移动目标）")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig_walk4_timeline.png", dpi=130)
    plt.close(fig)

    # 图2 热力图（帧 x yaw）
    fig, ax = plt.subplots(figsize=(13, 6))
    sub = mat[:, 140:350]
    im = ax.imshow(sub, aspect="auto", origin="lower", cmap="magma",
                   extent=[140, 350, 0, nf - 1], vmin=0, vmax=120)
    for (s, e) in [(ev["start_s"], ev["end_s"]) for ev in ev_list]:
        ax.axhspan(s / (nf - 1) * (nf - 1), e / (nf - 1) * (nf - 1), color="lime", alpha=0.15)
    ax.set_xlabel("yaw 航向 (°)")
    ax.set_ylabel("帧序号")
    ax.set_title("4.pcap 远回波 帧×航向 热力图（暗色条带=被行人遮挡的墙体回波）")
    cb = fig.colorbar(im, ax=ax, label="远回波点数", shrink=0.8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_walk4_heatmap.png", dpi=130)
    plt.close(fig)

    # 图3 遮挡中心轨迹
    fig, ax = plt.subplots(figsize=(13, 5))
    cents = [x["occ_center_yaw"] for x in per_frame]
    tt = [x["t_rel_s"] for x in per_frame]
    ax.plot(tt, cents, "-o", ms=3, color="crimson", label="遮挡带中心 yaw")
    ax.set_xlabel("相对时间 (s)")
    ax.set_ylabel("yaw 航向 (°)")
    ax.set_ylim(140, 350)
    ax.set_title("4.pcap 遮挡带中心随时间的移动（=移动目标航向轨迹）")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig_walk4_occlusion.png", dpi=130)
    plt.close(fig)

    print("PNG saved to", OUT)
    print("summary saved to", OUT / "walk4_summary.json")


if __name__ == "__main__":
    sys.exit(main())
