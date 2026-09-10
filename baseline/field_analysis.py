# -*- coding: utf-8 -*-
"""Analyze the 7-byte point format across packets: which field is angle/range/intensity.
Plot field sequences within and across packets."""
import struct
import sys
import numpy as np

H, P, N = 10, 7, 200  # header 10B, 200 points x 7B

def read_all(path, max_pkts=3000):
    out = []
    with open(path, 'rb') as f:
        f.read(24)
        while len(out) < max_pkts:
            hdr = f.read(16)
            if len(hdr) < 16:
                break
            ts_sec, ts_usec, incl, orig = struct.unpack('<IIII', hdr)
            data = f.read(incl)
            ihl = (data[14] & 0x0F) * 4
            pl = data[14 + ihl + 8:]
            out.append((ts_sec + ts_usec * 1e-6, pl))
    return out

def parse(pl):
    pts = []
    for i in range(N):
        o = H + i * P
        v1, v2, v3 = struct.unpack_from('<HHH', pl, o)
        v4 = pl[o + 6]
        pts.append((v1, v2, v3, v4))
    return pts

def main(path):
    pkts = read_all(path, 4000)
    print(f'packets: {len(pkts)}')
    dense = [(ts, pl) for ts, pl in pkts if sum(1 for b in pl if b) > 400]
    print(f'dense packets: {len(dense)}')

    # across 40 consecutive dense packets, track mean v1 and v2, and v3 max
    rows = []
    for ts, pl in dense[:40]:
        pts = parse(pl)
        v1 = np.array([p[0] for p in pts]); v2 = np.array([p[1] for p in pts])
        v3 = np.array([p[2] for p in pts]); v4 = np.array([p[3] for p in pts])
        rows.append((ts, v1.mean(), v1.min(), v1.max(), v2.mean(), v3.max(), v4.mean()))
    print('\n pkt | ts_delta(ms) | v1_mean | v1_min | v1_max | v2_mean | v3_max | v4_mean')
    prev = None
    for r in rows[:25]:
        dt = (r[0] - prev) * 1000 if prev else 0
        print(f'  {len([x for x in rows[:25] if x[0]<=r[0]])-1:3d}  | {dt:8.3f} | {r[1]:8.1f} | {r[2]:7d} | {r[3]:7d} | {r[4]:8.1f} | {r[5]:6d} | {r[6]:6.1f}')
        prev = r[0]

    # within one packet: field behavior
    ts, pl = dense[0]
    pts = parse(pl)
    print(f'\nOne dense packet ts={ts:.3f}:')
    v1 = [p[0] for p in pts]; v2 = [p[1] for p in pts]; v3 = [p[2] for p in pts]; v4 = [p[3] for p in pts]
    print('v1 first/last/unique:', v1[:5], '...', v1[-3:], 'unique=', len(set(v1)))
    print('v2 first/last/unique:', v2[:5], '...', v2[-3:], 'unique=', len(set(v2)))
    print('v3 first/last/unique:', v3[:5], '...', v3[-3:], 'unique=', len(set(v3)))
    print('v4 unique:', sorted(set(v4)))

    # header fields across packets
    print('\nHeader (first 10 bytes) across dense packets:')
    for ts, pl in dense[:8]:
        print(f'  ts={ts:.3f}  header={pl[:10].hex()}')

    # v3 vs v2 scatter idea: check range of v2 and v3
    all_v2 = np.concatenate([np.array([p[1] for p in parse(pl)]) for _, pl in dense[:10]])
    all_v3 = np.concatenate([np.array([p[2] for p in parse(pl)]) for _, pl in dense[:10]])
    print('\nv2 global: min', all_v2.min(), 'max', all_v2.max(), 'mean', round(all_v2.mean(),1))
    print('v3 global: min', all_v3.min(), 'max', all_v3.max(), 'mean', round(all_v3.mean(),1))

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')
