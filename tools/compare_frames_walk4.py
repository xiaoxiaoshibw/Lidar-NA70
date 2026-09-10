#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比安静帧 vs 事件帧：原始 (dcode,pcode,ycode) 结构，确认字段语义。"""
import struct
import sys

import numpy as np


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

    for fi in [5, 15, 25, 44, 50, 60]:
        g = frames[fi]
        d = np.concatenate([p[1] for p in g])
        p = np.concatenate([p[2] for p in g])
        y = np.concatenate([p[3] for p in g])
        rf = np.concatenate([p[4] for p in g])
        m = rf > 0
        print(f"\n===== 帧 {fi} =====")
        print(f"refl>0: {int(m.sum())}")
        # dcode 直方图（对数分段）
        hist, edges = np.histogram(d[m], bins=[0, 1, 20, 100, 300, 600, 1000, 2000, 4000, 8000, 20000, 65536])
        print("dcode 直方图:", dict(zip([f"{edges[i]}-{edges[i+1]}" for i in range(len(edges)-1)], hist.tolist())))
        # 远回波 (dcode>=300) 的 ycode/pcode 关系
        far = m & (d >= 300) & (d < 4000)
        if far.sum():
            print(f"远回波 {int(far.sum())}: ycode [{y[far].min()},{y[far].max()}] "
                  f"pcode [{p[far].min()},{p[far].max()}] dcode [{d[far].min()},{d[far].max()}]")
            # ycode vs dcode 散点采样
            s = np.random.RandomState(0).choice(far.sum(), min(3000, int(far.sum())), replace=False)
            ys, ds, ps = y[far][s], d[far][s], p[far][s]
            print("  采样 (ycode,dcode,pcode) 首20:")
            for a, b, c in zip(ys[:20], ds[:20], ps[:20]):
                print(f"    y={a:5d} d={b:5d} p={c:5d}")
        near = m & (d < 20)
        if near.sum():
            print(f"近回波 {int(near.sum())}: ycode [{y[near].min()},{y[near].max()}] "
                  f"pcode [{p[near].min()},{p[near].max()}]")
            s = np.random.RandomState(1).choice(near.sum(), min(20, int(near.sum())), replace=False)
            print("  采样 (ycode,dcode,pcode):", [(int(y[near][k]), int(d[near][k]), int(p[near][k])) for k in s])
        mid = m & (d >= 20) & (d < 300)
        if mid.sum():
            print(f"中回波 20-299: {int(mid.sum())}")


if __name__ == "__main__":
    main()
