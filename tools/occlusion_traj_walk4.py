#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4.pcap 遮挡轨迹提取：每帧远回波 vs 安静基线 -> 主遮挡带(中心yaw, 宽度, 深度)。

输出 walk4_occlusion_traj.csv: 帧, 相对时间, 遮挡中心yaw, 遮挡宽度, 遮挡深度(减少点数)
"""
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

    # 远回波 per-yaw 计数矩阵
    mat = np.zeros((nf, 360), dtype=np.float32)
    for i, g in enumerate(frames):
        dcode = np.concatenate([p[1] for p in g])
        pcode = np.concatenate([p[2] for p in g])
        ycode = np.concatenate([p[3] for p in g])
        refl = np.concatenate([p[4] for p in g])
        m = (refl > 0) & (dcode >= 300) & (dcode < 2000) & (pcode < 60000) & (ycode < 60000)
        yd = np.clip(np.floor(ycode[m].astype(np.float64) * YAW_RES).astype(np.int32), 0, 359)
        np.add.at(mat, (i, yd), 1.0)

    # 安静基线：只用明显安静帧（0-12 与 55-92，排除事件与末帧截断）
    quiet_idx = list(range(0, 13)) + list(range(55, 92))
    base = np.median(mat[quiet_idx], axis=0)          # (360,)
    base = np.maximum(base, 1e-6)

    # 每帧：缺口 profile = 1 - 当前/基线（限 0..1），找主连续缺口
    rows = []
    prof = np.zeros((nf, 360), dtype=np.float32)
    for i in range(nf):
        # 只统计基线可靠的 bin（墙回波 >= 5），其余置 0（结构间隙，不算遮挡）
        rel = base >= 5.0
        deficit = np.where(rel, np.clip(1.0 - mat[i] / np.maximum(base, 1e-6), 0, 1.0), 0.0)
        prof[i] = deficit
        # 平滑 + 阈值
        d = np.convolve(deficit, np.ones(3) / 3, mode="same")
        mask = d >= 0.35
        if not mask.any() or i in (93,):
            rows.append((i, frames[i][0][0] - t0, np.nan, np.nan, np.nan))
            continue
        # 合并连续段
        segs = []
        cur = [np.where(mask)[0][0]]
        idxs = np.where(mask)[0]
        for k in range(1, len(idxs)):
            if idxs[k] - idxs[k - 1] <= 2:
                cur.append(idxs[k])
            else:
                segs.append(cur)
                cur = [idxs[k]]
        segs.append(cur)
        # 取最深段（平均缺口最大）
        best = None
        for s in segs:
            if len(s) < 3:
                continue
            depth = float(np.mean(deficit[s]))
            if best is None or depth > best[0]:
                best = (depth, s)
        if best is None:
            rows.append((i, frames[i][0][0] - t0, np.nan, np.nan, np.nan))
            continue
        depth, s = best
        center = float(np.mean(s))
        width = float(s[-1] - s[0] + 1)
        rows.append((i, frames[i][0][0] - t0, center, width, depth))

    rows = np.array(rows, dtype=float)
    with open(OUT / "walk4_occlusion_traj.csv", "w") as f:
        f.write("frame,t_rel_s,occ_center_yaw_deg,occ_width_deg,occ_depth\n")
        for r in rows:
            f.write(f"{int(r[0])},{r[1]:.3f},{r[2]:.2f},{r[3]:.2f},{r[4]:.3f}\n")
    np.save(OUT / "walk4_occ_prof.npy", prof)
    np.save(OUT / "walk4_occ_base.npy", base)

    print(f"{'帧':>4s} {'t(s)':>7s} {'遮挡中心yaw':>10s} {'宽度(°)':>8s} {'深度':>6s}")
    for r in rows:
        if np.isnan(r[2]):
            continue
        print(f"{int(r[0]):4d} {r[1]:7.2f} {r[2]:10.1f} {r[3]:8.1f} {r[4]:6.2f}")


if __name__ == "__main__":
    main()
