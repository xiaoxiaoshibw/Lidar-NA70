#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4.pcap 遮挡带分析：按帧 x yaw-bin 统计远回波/近回波数量矩阵。

近回波: dcode 1..19 (0.01-0.19m 或近场标记)
远回波: dcode 300..2000 (3-20m, 墙/目标)
输出:
  walk4_occ_far.npy   (nframe, 360) 每帧每yaw度 远回波点数
  walk4_occ_near.npy  (nframe, 360) 近回波点数
  walk4_occ_meta.json 各帧总数
"""
import json
import struct
import sys

import numpy as np

PITCH_RES = 0.0258
YAW_RES = 0.0432
DIST_RES_M = 0.01


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
    out = r"D:\Code\radar\tools\analysis_output"
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
    far_mat = np.zeros((nf, 360), dtype=np.int32)
    near_mat = np.zeros((nf, 360), dtype=np.int32)
    d3m_mat = np.zeros((nf, 360), dtype=np.int32)   # 3-8m 内 3m 边界
    per_frame = []
    t_rel = []
    t0 = frames[0][0][0]
    for i, g in enumerate(frames):
        dcode = np.concatenate([p[1] for p in g])
        pcode = np.concatenate([p[2] for p in g])
        ycode = np.concatenate([p[3] for p in g])
        refl = np.concatenate([p[4] for p in g])
        t_rel.append(g[0][0] - t0)
        m = refl > 0
        d, p, y = dcode[m], pcode[m], ycode[m]
        yd = np.floor(y.astype(np.float64) * YAW_RES).astype(np.int32)
        yd = np.clip(yd, 0, 359)
        near = d < 20
        far = (d >= 300) & (d < 2000) & (p < 60000) & (y < 60000)
        np.add.at(near_mat, (i, yd[near]), 1)
        np.add.at(far_mat, (i, yd[far]), 1)
        per_frame.append((int(m.sum()), int(near.sum()), int(far.sum())))
    per_frame = np.array(per_frame, dtype=np.int32)
    np.save(rf"{out}\walk4_occ_far.npy", far_mat)
    np.save(rf"{out}\walk4_occ_near.npy", near_mat)
    np.save(rf"{out}\walk4_occ_t.npy", np.array(t_rel, dtype=np.float32))
    np.save(rf"{out}\walk4_occ_perframe.npy", per_frame)
    with open(rf"{out}\walk4_occ_meta.json", "w") as f:
        json.dump({"nframes": nf, "duration_s": float(t_rel[-1])}, f)

    print(f"帧数 {nf}, 时长 {t_rel[-1]:.2f}s")
    print(f"{'帧':>4s} {'t(s)':>6s} {'全部':>7s} {'近(<20)':>8s} {'远(3-20m)':>9s} "
          f"{'远yaw范围':>16s} {'近yaw范围':>16s}")
    for i in range(0, nf, 5):
        a, b, c = per_frame[i]
        fy = np.where(far_mat[i] > 0)[0]
        ny = np.where(near_mat[i] > 0)[0]
        fyr = f"{fy.min()}-{fy.max()}" if len(fy) else "-"
        nyr = f"{ny.min()}-{ny.max()}" if len(ny) else "-"
        print(f"{i:4d} {t_rel[i]:6.2f} {a:7d} {b:8d} {c:9d} {fyr:>16s} {nyr:>16s}")


if __name__ == "__main__":
    main()
