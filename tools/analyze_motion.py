#!/usr/bin/env python3
"""NA70 固定雷达移动目标检测 —— 对 2.pcap 做完整离线分析。

方法（与项目方案一致）：
  1. 每 100 包合成一帧（驱动 kCachePacketCount=100，≈10 Hz）；
  2. 按驱动协议把每点 (距离, 俯仰, 航向) 码值换算为笛卡尔坐标；
  3. 前 warmup 帧建立体素背景模型，后续帧差分得到移动点；
  4. 移动点 DBSCAN 聚类 → 簇中心；
  5. 簇中心按最近邻做轨迹关联；
  6. 输出摘要、CSV 与可视化图。

用法：python analyze_motion.py <pcap> [--outdir <dir>]
"""
import argparse
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

# ---------- 协议常量（与驱动 inno_device_lidar_node.cpp 一致） ----------
PITCH_RES = 0.0258      # 度/码
YAW_RES = 0.0432        # 度/码
DIST_RES_M = 16.0 / 1000.0  # 米/码
POINTS_PER_PKT = 200
PKT_DATA_BYTES = POINTS_PER_PKT * 7  # 1400
TS_IDX = 1400
PKT_TOTAL = 1410
FRAME_PKTS = 100  # 驱动攒包数

# ---------- 检测参数（源自项目 motion_detector.yaml / motion_core.py） ----------
VOXEL = 0.15
WARMUP_FRAMES = 50       # 5 秒背景热身
MIN_RANGE, MAX_RANGE = 0.5, 100.0
CLUSTER_EPS = 0.5
CLUSTER_MIN_SAMPLES = 5
MIN_MOVING_POINTS = 30
TRACK_GATE = 2.0         # 轨迹关联门限（米）
MIN_TRACK_FRAMES = 10    # 轨迹最少持续帧数


def iter_udp_payloads(path):
    """流式产出 (ts_pcap, payload)。仅 Ethernet/IPv4/UDP。"""
    with open(path, "rb") as f:
        hdr = f.read(24)
        magic, = struct.unpack("<I", hdr[:4])
        if magic == 0xA1B2C3D4:
            endian, ns = "<", False
        elif magic == 0xD4C3B2A1:
            endian, ns = ">", False
        elif magic == 0xA1B23C4D:
            endian, ns = "<", True
        else:
            endian, ns = ">", True
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


def parse_packet_timestamp(payload):
    """设备内部时钟：6 字节大端秒 + 2 字节毫秒（同步位不置位时）。"""
    b = payload[TS_IDX:TS_IDX + 8]
    sec = (b[0] & 0x7F)
    for i in range(1, 6):
        sec = (sec << 8) | b[i]
    ms = (b[6] << 8) | b[7]
    return sec + ms / 1000.0


def frame_cloud(payloads):
    """把一帧的 payload 列表解析为 [N, 4] 点云：x, y, z, refl。"""
    data = b"".join(p[:PKT_DATA_BYTES] for p in payloads)
    rb = np.frombuffer(data, dtype=np.uint8).reshape(-1, 7)
    dcode = (rb[:, 0].astype(np.uint32) << 8) | rb[:, 1]
    pcode = (rb[:, 2].astype(np.uint32) << 8) | rb[:, 3]
    ycode = (rb[:, 4].astype(np.uint32) << 8) | rb[:, 5]
    refl = rb[:, 6].astype(np.float32)

    d = dcode.astype(np.float64) * DIST_RES_M
    pitch = pcode.astype(np.float64) * PITCH_RES * np.pi / 180.0
    yaw = ycode.astype(np.float64) * YAW_RES * np.pi / 180.0

    x = d * np.cos(yaw) * np.cos(pitch)
    y = d * np.sin(yaw) * np.cos(pitch)
    z = d * np.sin(pitch)
    return np.column_stack([x, y, z, refl])


def pack_voxel(v):
    """int32 体素坐标打包为 int64（保证无碰撞）。"""
    v = v.astype(np.int64)
    return (v[:, 0] << 42) | (v[:, 1] << 21) | v[:, 2]


def dbscan_clusters(points, eps=0.5, min_samples=5):
    """简化的 DBSCAN（cKDTree + BFS），返回簇标签数组。"""
    n = len(points)
    if n == 0:
        return np.zeros(0, dtype=np.int32)
    labels = np.full(n, -1, dtype=np.int32)
    tree = cKDTree(points)
    label = 0
    for i in range(n):
        if labels[i] != -1:
            continue
        nb = tree.query_ball_point(points[i], eps)
        if len(nb) < min_samples:
            continue  # 噪声
        labels[i] = label
        stack = [i]
        while stack:
            j = stack.pop()
            nbs = tree.query_ball_point(points[j], eps)
            for k in nbs:
                if labels[k] == -1:
                    labels[k] = label
                    stack.append(k)
        label += 1
    return labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", nargs="?", default=r"D:\Code\radar\radar_capture_data\2.pcap")
    ap.add_argument("--outdir", default=r"D:\Code\radar\tools\analysis_output")
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    t0 = time.time()
    frames_xyz = []      # 每帧点云（供移动点回溯，避免存全部帧）
    frame_ts = []        # 每帧设备时间戳
    bg = None            # 背景体素 packed set
    bg_packed = None
    per_frame = []       # (frame, t, considered, moving, ratio)
    moving_clusters = []  # 每帧 [(cx, cy, cz, npts)]
    all_moving_points = []  # (t, x, y, z) 供 BEV 图

    pkt_buf = []
    frame_idx = 0
    warmup_done = False
    considered_total = 0
    moving_total = 0

    for _pcap_ts, payload in iter_udp_payloads(args.pcap):
        if len(payload) < PKT_TOTAL:
            continue
        pkt_buf.append(payload)
        if len(pkt_buf) < FRAME_PKTS:
            continue
        # 满 100 包 → 一帧
        ts_dev = parse_packet_timestamp(pkt_buf[0])
        cloud = frame_cloud(pkt_buf)
        pkt_buf.clear()

        d = np.linalg.norm(cloud[:, :3], axis=1)
        valid = (d >= MIN_RANGE) & (d <= MAX_RANGE) & np.isfinite(cloud[:, :3]).all(axis=1)
        pts = cloud[valid]
        considered_total += len(pts)

        if not warmup_done:
            # 热身期：全部入背景
            vox = np.floor(pts[:, :3] / VOXEL).astype(np.int64)
            if bg is None:
                bg = set(map(int, pack_voxel(np.unique(vox, axis=0))))
            else:
                bg |= set(map(int, pack_voxel(np.unique(vox, axis=0))))
            warmup_done = len(bg) > 0 or frame_idx >= WARMUP_FRAMES - 1
            if frame_idx >= WARMUP_FRAMES - 1 and not warmup_done:
                warmup_done = True
            frame_idx += 1
            per_frame.append((frame_idx, ts_dev, len(pts), 0, 0.0))
            frames_xyz.append(None)
            frame_ts.append(ts_dev)
            continue

        # 差分
        if bg_packed is None:
            bg_packed = np.array(sorted(bg), dtype=np.int64)
        vox = np.floor(pts[:, :3] / VOXEL).astype(np.int64)
        packed = pack_voxel(vox)
        uniq, inv = np.unique(packed, return_inverse=True)
        is_bg = np.isin(uniq, bg_packed)
        moving_mask = ~is_bg[inv]
        moving_pts = pts[moving_mask]

        moving_total += len(moving_pts)
        ratio = len(moving_pts) / len(pts) if len(pts) else 0.0
        per_frame.append((frame_idx, ts_dev, len(pts), len(moving_pts), ratio))

        # 移动点聚类
        clusters = []
        if len(moving_pts) >= CLUSTER_MIN_SAMPLES:
            labels = dbscan_clusters(moving_pts[:, :3], CLUSTER_EPS, CLUSTER_MIN_SAMPLES)
            for lab in range(labels.max() + 1):
                sel = moving_pts[labels == lab]
                if len(sel) >= CLUSTER_MIN_SAMPLES:
                    c = sel[:, :3].mean(axis=0)
                    clusters.append((c[0], c[1], c[2], len(sel)))
        moving_clusters.append(clusters)
        if len(moving_pts):
            all_moving_points.append(
                np.column_stack([np.full(len(moving_pts), ts_dev), moving_pts[:, :3]])
            )

        # 背景模型滚动更新：静态点（命中背景的）刷新，但保持冻结式背景
        # （项目 motion_core 为冻结背景，这里保持一致，不再增量吸收）
        frame_idx += 1
        frames_xyz.append(pts[moving_mask].copy() if len(moving_pts) else None)
        frame_ts.append(ts_dev)

        if frame_idx % 200 == 0:
            print(f"  帧 {frame_idx}: considered={len(pts)} moving={len(moving_pts)} "
                  f"ratio={ratio:.4f} clusters={len(clusters)} ({time.time()-t0:.0f}s)")

    print(f"\n解析完成：{frame_idx} 帧，耗时 {time.time()-t0:.0f} s")
    print(f"总点数(过滤后) {considered_total}，总移动点数 {moving_total}，"
          f"移动占比 {100*moving_total/max(considered_total,1):.3f}%")

    # ---------- 汇总 ----------
    arr = np.array(per_frame, dtype=np.float64)  # frame, t, considered, moving, ratio
    active = arr[arr[:, 0] > WARMUP_FRAMES]
    moving_frames = active[active[:, 3] >= MIN_MOVING_POINTS]
    print(f"有效帧（warmup 后）: {len(active)}")
    print(f"移动点数 >= {MIN_MOVING_POINTS} 的帧: {len(moving_frames)} "
          f"({100*len(moving_frames)/max(len(active),1):.1f}%)")
    if len(active):
        print(f"移动点/帧: 均值 {active[:,3].mean():.1f} 中位 {np.median(active[:,3]):.1f} "
              f"最大 {active[:,3].max():.0f}")

    # ---------- 轨迹关联 ----------
    tracks = []  # 每条: list[(t, cx, cy, cz, npts)]
    for fi, clusters in enumerate(moving_clusters):
        t_frame = arr[fi, 1]
        if not clusters:
            continue
        cents = np.array([[c[0], c[1], c[2]] for c in clusters])
        if not tracks:
            for c in clusters:
                tracks.append([(t_frame, c[0], c[1], c[2], c[3])])
            continue
        # 与上一帧所有轨迹尾部匹配
        tails = np.array([tr[-1][1:4] for tr in tracks])
        tree = cKDTree(tails)
        assigned = set()
        used_tr = set()
        for ci, c in enumerate(clusters):
            dists, idxs = tree.query(c[:3], k=1)
            if idxs not in used_tr and dists <= TRACK_GATE:
                tracks[idxs].append((t_frame, c[0], c[1], c[2], c[3]))
                used_tr.add(idxs)
                assigned.add(ci)
        for ci, c in enumerate(clusters):
            if ci not in assigned:
                tracks.append([(t_frame, c[0], c[1], c[2], c[3])])
    long_tracks = [tr for tr in tracks if len(tr) >= MIN_TRACK_FRAMES]
    print(f"轨迹总数（簇级）: {len(tracks)}，持续 >= {MIN_TRACK_FRAMES} 帧的轨迹: {len(long_tracks)}")
    if long_tracks:
        for i, tr in enumerate(long_tracks[:8]):
            t0t, x0, y0, z0, _ = tr[0]
            t1t, x1, y1, z1, _ = tr[-1]
            dt = t1t - t0t
            dist = np.hypot(x1 - x0, y1 - y0)
            speed = dist / dt if dt > 0 else 0
            print(f"  轨迹{i}: {len(tr)}帧 历时{dt:.1f}s 位移{dist:.1f}m "
                  f"速度{speed:.2f}m/s 起点({x0:.1f},{y0:.1f},{z0:.1f})→终点({x1:.1f},{y1:.1f},{z1:.1f})")

    # ---------- CSV ----------
    csv_path = outdir / "motion_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        f.write("frame,device_t_s,considered,moving,moving_ratio,n_clusters\n")
        for i, (fr, ts_, cons, mov, rat) in enumerate(arr):
            f.write(f"{int(fr)},{ts_:.3f},{int(cons)},{int(mov)},{rat:.6f},"
                    f"{len(moving_clusters[i]) if i < len(moving_clusters) else 0}\n")
    print(f"CSV 已保存: {csv_path}")

    # ---------- 图1：移动点时间序列 ----------
    t_ax = arr[:, 1] - arr[0, 1]
    fig, ax1 = plt.subplots(figsize=(12, 4.5))
    ax1.plot(t_ax, arr[:, 3], lw=0.8, color="#1f77b4", label="移动点数/帧")
    ax1.set_xlabel("抓包时间 (s)")
    ax1.set_ylabel("移动点数", color="#1f77b4")
    ax1.axvspan(0, WARMUP_FRAMES / 10, alpha=0.15, color="gray", label=f"热身期({WARMUP_FRAMES}帧)")
    ax2 = ax1.twinx()
    ax2.plot(t_ax, arr[:, 4], lw=0.8, color="#d62728", alpha=0.7, label="移动占比")
    ax2.set_ylabel("移动点占比", color="#d62728")
    ax2.set_ylim(0, max(0.1, arr[:, 4].max() * 1.2))
    ax1.set_title("NA70 2.pcap：每帧移动点数与占比（体素背景减除, voxel=0.15m）")
    fig.tight_layout()
    p1 = outdir / "fig1_moving_timeseries.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)

    # ---------- 图2：BEV 移动点分布（按时间着色） ----------
    if all_moving_points:
        mp = np.vstack(all_moving_points)
        t_norm = (mp[:, 0] - mp[:, 0].min()) / max(mp[:, 0].max() - mp[:, 0].min(), 1e-9)
        fig, ax = plt.subplots(figsize=(10, 8))
        sc = ax.scatter(mp[:, 1], mp[:, 2], c=t_norm, s=1.5, cmap="turbo", alpha=0.5)
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        ax.set_aspect("equal")
        ax.set_title(f"移动点俯视图（共 {len(mp)} 点，颜色=时间）")
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("相对时间")
        fig.tight_layout()
        p2 = outdir / "fig2_moving_points_bev.png"
        fig.savefig(p2, dpi=130)
        plt.close(fig)

    # ---------- 图3：长轨迹 ----------
    if long_tracks:
        fig, ax = plt.subplots(figsize=(10, 8))
        for i, tr in enumerate(long_tracks[:12]):
            xs = [p[1] for p in tr]
            ys = [p[2] for p in tr]
            ax.plot(xs, ys, lw=1.6, marker="o", ms=1.6, label=f"目标{i+1} ({len(tr)}帧)")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        ax.set_aspect("equal")
        ax.set_title(f"移动目标轨迹（持续 >= {MIN_TRACK_FRAMES} 帧的簇中心）")
        ax.legend(fontsize=8, loc="upper right")
        fig.tight_layout()
        p3 = outdir / "fig3_tracks.png"
        fig.savefig(p3, dpi=130)
        plt.close(fig)

        # 图4：轨迹 x/y 随帧变化
        fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
        for i, tr in enumerate(long_tracks[:6]):
            ts_ = [p[0] - long_tracks[0][0][0] for p in tr]
            xs = [p[1] for p in tr]
            ys = [p[2] for p in tr]
            axes[0].plot(ts_, xs, lw=1.2, label=f"目标{i+1}")
            axes[1].plot(ts_, ys, lw=1.2, label=f"目标{i+1}")
        axes[0].set_ylabel("x (m)")
        axes[1].set_ylabel("y (m)")
        axes[1].set_xlabel("相对时间 (s)")
        axes[0].legend(fontsize=8)
        axes[1].legend(fontsize=8)
        axes[0].set_title("移动目标位置随时间变化")
        fig.tight_layout()
        p4 = outdir / "fig4_track_xy_time.png"
        fig.savefig(p4, dpi=130)
        plt.close(fig)

    # ---------- 结论 ----------
    has_motion = len(moving_frames) > 0 and (
        len(long_tracks) > 0 or moving_frames[:, 3].max() >= 100
    )
    print("\n================ 结论 ================")
    if has_motion:
        print(f"检测到移动物体：warmup 后有 {len(moving_frames)} 帧移动点数≥{MIN_MOVING_POINTS}，"
              f"峰值移动点 {moving_frames[:,3].max():.0f}，长轨迹 {len(long_tracks)} 条")
    else:
        print("未检测到明显移动物体（移动点不足或均为瞬时噪声）")
    print(f"结果文件输出目录: {outdir}")


if __name__ == "__main__":
    sys.exit(main())
