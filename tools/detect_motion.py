#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NA70 激光雷达 —— 移动目标识别脚本（通用版）

原理：远回波遮挡带分析
  固定背景（墙体等远距回波）被移动目标遮挡 → 逐帧远回波分布与安静基线对比
  → 检测遮挡带 → 加权圆平均求遮挡中心航向 → 连续低回波段合并为移动事件。

适用：NA70 双楔镜激光雷达 pcap 抓包（UDP 7000→2368, payload 1410B, 200点×7B 大端）。

用法：
  python detect_motion.py <pcap文件>
  python detect_motion.py <pcap文件> --output D:\\out --blind-dcode 468 \\
      --yaw-min 140 --yaw-max 350 --event-threshold 0.75 --min-event-frames 3

输出：
  控制台：移动事件摘要
  <output>/motion_<stem>_summary.json  逐帧指标 + 事件
  <output>/fig_motion_<stem>_timeline.png  回波数时间线（标注事件区间）
  <output>/fig_motion_<stem>_trajectory.png  遮挡中心航向轨迹
"""
import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

# ---------- 协议常量（NA70 大端） ----------
YAW_RES = 0.0432          # 航向码 → 度
DIST_RES_M = 0.01         # 距离码 → 米
PAYLOAD_MIN = 1410        # 有效雷达包最小长度
FRAME_TOL_S = 0.25        # 分帧时间容差


# ---------- 1. pcap 解析 ----------
def iter_udp_payload(path):
    """从 pcap 文件中逐个产出 UDP payload 字节。"""
    with open(path, "rb") as f:
        f.read(24)  # pcap global header
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                break
            _, _, caplen, _ = struct.unpack("<IIII", ph)
            data = f.read(caplen)
            if len(data) < caplen:
                break
            # 以太网 + IPv4 + UDP
            if len(data) < 42 or struct.unpack(">H", data[12:14])[0] != 0x0800:
                continue
            ihl = (data[14] & 0x0F) * 4
            if data[14 + 9] != 17:  # UDP
                continue
            ulen = struct.unpack(">H", data[14 + ihl + 4:14 + ihl + 6])[0]
            yield data[14 + ihl + 8:14 + ihl + ulen]


def dev_timestamp(pl):
    """NA70 设备时间戳（秒，相对设备启动）。"""
    tsb = pl[1400:1408]
    seconds = (tsb[0] & 0x7F) << 40
    for i in range(1, 6):
        seconds |= tsb[i] << (8 * (5 - i))
    ms = (tsb[6] << 8) | tsb[7]
    return seconds + ms / 1000.0


def parse_packets(path):
    """解析 pcap，返回 [(ts, dcode, pcode, ycode, refl), ...]，按时间排序。"""
    pkts = []
    for pl in iter_udp_payload(path):
        if len(pl) < PAYLOAD_MIN:
            continue
        rb = np.frombuffer(pl[:1400], dtype=np.uint8).reshape(-1, 7)
        dcode = (rb[:, 0].astype(np.uint32) << 8) | rb[:, 1]
        pcode = (rb[:, 2].astype(np.uint32) << 8) | rb[:, 3]
        ycode = (rb[:, 4].astype(np.uint32) << 8) | rb[:, 5]
        refl = rb[:, 6]
        pkts.append((dev_timestamp(pl), dcode, pcode, ycode, refl))
    pkts.sort(key=lambda x: x[0])
    return pkts


# ---------- 2. 分帧 ----------
def split_frames(pkts, tol=FRAME_TOL_S):
    """按时间容差分帧，返回 [[pkt, ...], ...]。"""
    frames = []
    for p in pkts:
        if not frames or abs(p[0] - frames[-1][0][0]) > tol:
            frames.append([p])
        else:
            frames[-1].append(p)
    return frames


# ---------- 3. 远回波矩阵 ----------
def build_far_matrix(frames, blind_dcode, yaw_min, yaw_max):
    """
    构建 (帧数, 360) 远回波计数矩阵 + 每帧统计。
    远回波条件：refl>0 且 blind_dcode <= dcode < 2000 且 pcode<60000 且 ycode<60000。
    """
    nf = len(frames)
    mat = np.zeros((nf, 360), dtype=np.float32)
    tot = np.zeros(nf, dtype=np.int32)
    near = np.zeros(nf, dtype=np.int32)
    for i, g in enumerate(frames):
        dcode = np.concatenate([p[1] for p in g])
        pcode = np.concatenate([p[2] for p in g])
        ycode = np.concatenate([p[3] for p in g])
        refl = np.concatenate([p[4] for p in g])
        m = refl > 0
        tot[i] = int(m.sum())
        near[i] = int((m & (dcode == 0)).sum())
        fm = (m & (dcode >= blind_dcode) & (dcode < 2000)
              & (pcode < 60000) & (ycode < 60000))
        yd = np.clip(np.floor(ycode[fm].astype(np.float64) * YAW_RES).astype(np.int32), 0, 359)
        np.add.at(mat, (i, yd), 1.0)
    t = np.array([g[0][0] - frames[0][0][0] for g in frames], dtype=np.float32)
    return mat, t, tot, near


# ---------- 4. 自动检测安静帧 ----------
def detect_quiet_frames(far_tot, quantile=75, ratio=0.9):
    """
    自动选取安静帧：远回波总数 >= 上四分位数 * ratio 的帧。
    这些帧作为背景基线。
    """
    q = np.percentile(far_tot, quantile)
    return far_tot >= q * ratio


# ---------- 5. 逐帧遮挡指标 ----------
def compute_occlusion(mat, base, rel, yaw_min, yaw_max):
    """
    逐帧计算遮挡指标：
      deficit   — 相对缺口总和（0~360）
      center    — 遮挡带中心航向（加权圆平均），无遮挡时 None
      band_s/e  — 最长连续遮挡带起止航向（平滑后 deficit>=0.5，长度>=3）
    """
    nf = mat.shape[0]
    per_frame = []
    for i in range(nf):
        defc = np.where(rel, np.maximum(0.0, base - mat[i]) / base, 0.0)
        # 遮挡中心：在 yaw 分析范围内加权圆平均
        w = defc[yaw_min:yaw_max]
        deg = np.arange(yaw_min, yaw_max)
        center = None
        if w.sum() > 1e-9:
            r = np.sum(w * np.exp(1j * np.deg2rad(deg)))
            center = round(float(np.rad2deg(np.angle(r)) % 360), 1)
        # 遮挡带：3 点平滑后 deficit >= 0.5 的最长连续段
        d = np.convolve(defc, np.ones(3) / 3, mode="same")
        idxs = np.where(d >= 0.5)[0]
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
            s = max(segs, key=len)
            if len(s) >= 3:
                band_s, band_e = int(s[0]), int(s[-1])
        per_frame.append({
            "frame": i,
            "deficit": round(float(defc.sum()), 3),
            "occ_center_yaw": center,
            "band_yaw_start": band_s,
            "band_yaw_end": band_e,
        })
    return per_frame


# ---------- 6. 事件检测 ----------
def detect_events(per_frame, far_tot, t, quiet_median, threshold_ratio, min_frames):
    """
    远回波连续低于 quiet_median * threshold_ratio 的帧段 → 移动事件。
    过滤掉长度 < min_frames 的短段。
    """
    thr = quiet_median * threshold_ratio
    low = far_tot < thr
    events = []
    cur = None
    for i in range(len(low)):
        if low[i]:
            if cur is None:
                cur = [i, i]
            cur[1] = i
        else:
            if cur is not None:
                if cur[1] - cur[0] + 1 >= min_frames:
                    events.append(tuple(cur))
                cur = None
    if cur is not None and cur[1] - cur[0] + 1 >= min_frames:
        events.append(tuple(cur))

    ev_list = []
    for (s, e) in events:
        seg = per_frame[s:e + 1]
        cents = [x["occ_center_yaw"] for x in seg if x["occ_center_yaw"] is not None]
        bands = [(x["band_yaw_start"], x["band_yaw_end"])
                 for x in seg if x["band_yaw_start"] is not None]
        far_seg = far_tot[s:e + 1]
        min_idx = int(np.argmin(far_seg))
        ev_list.append({
            "start_s": round(float(t[s]), 3),
            "end_s": round(float(t[e]), 3),
            "frames": f"{s}-{e}",
            "duration_s": round(float(t[e] - t[s]), 3),
            "min_far": int(far_seg[min_idx]),
            "min_far_t": round(float(t[s + min_idx]), 3),
            "drop_pct": round(float(1 - far_seg[min_idx] / quiet_median) * 100, 1),
            "occ_center_range": [round(min(cents), 1), round(max(cents), 1)] if cents else None,
            "band_extent": [min(b[0] for b in bands), max(b[1] for b in bands)] if bands else None,
        })
    return ev_list


# ---------- 7. 可视化 ----------
def plot_results(t, far_tot, tot, near, per_frame, events, quiet_median,
                 threshold_ratio, out_dir, stem):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    thr = quiet_median * threshold_ratio

    # 图1：时间线
    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(t, far_tot, "-", lw=1.4, color="steelblue", label="远回波数（背景/墙体）")
    ax.plot(t, tot, "-", lw=1.0, color="#9aa7b8", alpha=0.7, label="总有效点数")
    ax.axhline(quiet_median, ls="--", color="steelblue", lw=0.8, label=f"安静基线 {quiet_median:.0f}")
    ax.axhline(thr, ls=":", color="crimson", lw=0.8, label=f"事件阈值 {thr:.0f} ({threshold_ratio*100:.0f}%)")
    for ev in events:
        ax.axvspan(ev["start_s"], ev["end_s"], color="crimson", alpha=0.12)
    ax.set_xlabel("相对时间 (s)")
    ax.set_ylabel("点数")
    ax.set_title(f"{stem} 回波数时间线（红色区间 = 检测到移动目标）")
    ax.legend(fontsize=9, loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / f"fig_motion_{stem}_timeline.png", dpi=130)
    plt.close(fig)

    # 图2：遮挡中心轨迹
    fig, ax = plt.subplots(figsize=(13, 5))
    cents = [x["occ_center_yaw"] for x in per_frame]
    ax.plot(t, cents, "-o", ms=3, color="crimson", label="遮挡带中心 yaw")
    for ev in events:
        ax.axvspan(ev["start_s"], ev["end_s"], color="crimson", alpha=0.08)
    ax.set_xlabel("相对时间 (s)")
    ax.set_ylabel("yaw 航向 (°)")
    ax.set_ylim(0, 360)
    ax.set_title(f"{stem} 遮挡带中心随时间移动（= 移动目标航向轨迹）")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out_dir / f"fig_motion_{stem}_trajectory.png", dpi=130)
    plt.close(fig)


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="NA70 激光雷达移动目标识别")
    ap.add_argument("pcap", help="输入 pcap 文件路径")
    ap.add_argument("--output", default=None, help="输出目录（默认：pcap 同目录下 motion_output）")
    ap.add_argument("--blind-dcode", type=int, default=468,
                    help="盲区距离码阈值（默认 468 = 4.68m，以下无回波）")
    ap.add_argument("--yaw-min", type=int, default=140, help="yaw 分析范围下限（度）")
    ap.add_argument("--yaw-max", type=int, default=350, help="yaw 分析范围上限（度）")
    ap.add_argument("--event-threshold", type=float, default=0.75,
                    help="事件阈值：远回波低于安静基线 * 此值 即触发（默认 0.75）")
    ap.add_argument("--min-event-frames", type=int, default=3,
                    help="最小事件帧数（默认 3，过滤瞬时噪声）")
    args = ap.parse_args()

    pcap_path = Path(args.pcap)
    if not pcap_path.exists():
        print(f"错误：文件不存在 {pcap_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output) if args.output else pcap_path.parent / "motion_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pcap_path.stem

    print(f"[1/5] 解析 pcap: {pcap_path}")
    pkts = parse_packets(pcap_path)
    print(f"      有效雷达包: {len(pkts)}")
    if len(pkts) < 10:
        print("错误：有效包太少，可能不是 NA70 格式或文件为空", file=sys.stderr)
        sys.exit(1)

    print("[2/5] 分帧 ...")
    frames = split_frames(pkts)
    nf = len(frames)
    duration = float(frames[-1][0][0] - frames[0][0][0])
    fps = nf / duration if duration > 0 else 0
    print(f"      帧数: {nf}, 时长: {duration:.2f}s, 帧率: {fps:.2f} Hz")

    print(f"[3/5] 构建远回波矩阵 (盲区 dcode>={args.blind_dcode}, "
          f"yaw {args.yaw_min}~{args.yaw_max}) ...")
    mat, t, tot, near = build_far_matrix(frames, args.blind_dcode, args.yaw_min, args.yaw_max)
    far_tot = mat.sum(axis=1)

    print("[4/5] 自动检测安静帧 + 计算遮挡 ...")
    quiet = detect_quiet_frames(far_tot)
    quiet_median = float(np.median(far_tot[quiet]))
    base = np.maximum(np.median(mat[quiet], axis=0), 1e-6)
    rel = base >= 5.0  # 可靠 yaw bin
    print(f"      安静帧: {int(quiet.sum())}/{nf}, 安静基线远回波中位: {quiet_median:.0f}")

    per_frame = compute_occlusion(mat, base, rel, args.yaw_min, args.yaw_max)
    # 把每帧的统计补进去
    for i, pf in enumerate(per_frame):
        pf["t_rel_s"] = round(float(t[i]), 3)
        pf["total_pts"] = int(tot[i])
        pf["near_pts"] = int(near[i])
        pf["far_pts"] = int(far_tot[i])

    print(f"[5/5] 事件检测 (阈值 {args.event_threshold*100:.0f}%, 最小 {args.min_event_frames} 帧) ...")
    events = detect_events(per_frame, far_tot, t, quiet_median,
                           args.event_threshold, args.min_event_frames)

    # ---------- 输出 ----------
    summary = {
        "file": str(pcap_path),
        "packets": len(pkts),
        "frames": nf,
        "duration_s": round(duration, 2),
        "frame_rate_hz": round(fps, 2),
        "params": {
            "blind_dcode": args.blind_dcode,
            "blind_distance_m": round(args.blind_dcode * DIST_RES_M, 2),
            "yaw_range_deg": [args.yaw_min, args.yaw_max],
            "event_threshold": args.event_threshold,
            "min_event_frames": args.min_event_frames,
        },
        "quiet_frames": int(quiet.sum()),
        "quiet_far_median": round(quiet_median, 1),
        "events": events,
        "per_frame": per_frame,
    }
    json_path = out_dir / f"motion_{stem}_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    plot_results(t, far_tot, tot, near, per_frame, events, quiet_median,
                 args.event_threshold, out_dir, stem)

    # ---------- 控制台摘要 ----------
    print("\n" + "=" * 60)
    print(f"  移动目标识别结果 — {pcap_path.name}")
    print("=" * 60)
    print(f"  包数: {len(pkts)} | 帧数: {nf} | 时长: {duration:.2f}s | 帧率: {fps:.2f} Hz")
    print(f"  盲区: dcode>={args.blind_dcode} ({args.blind_dcode*DIST_RES_M:.2f}m)")
    print(f"  安静基线远回波中位: {quiet_median:.0f}")
    print(f"  检测到 {len(events)} 个移动事件:")
    for i, ev in enumerate(events, 1):
        cr = ev["occ_center_range"]
        cr_str = f"{cr[0]}°~{cr[1]}°" if cr else "N/A"
        print(f"    事件{i}: {ev['start_s']:.2f}~{ev['end_s']:.2f}s "
              f"(帧{ev['frames']}, 持续{ev['duration_s']:.2f}s)")
        print(f"           远回波最低 {ev['min_far']}@{ev['min_far_t']:.2f}s "
              f"(-{ev['drop_pct']}%) | 遮挡中心 {cr_str}")
    print("=" * 60)
    print(f"  输出目录: {out_dir}")
    print(f"    {json_path.name}")
    print(f"    fig_motion_{stem}_timeline.png")
    print(f"    fig_motion_{stem}_trajectory.png")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
