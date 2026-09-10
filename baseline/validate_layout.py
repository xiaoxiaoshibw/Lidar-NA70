# -*- coding: utf-8 -*-
"""Strict layout validation: H=10 header + 200 x 7B points, fields (u16 az?, u16 rng?, u8 amp, u16 cnt).
Check continuity across points and packet boundaries."""
import struct
import sys
import numpy as np

H, P, N = 10, 7, 200

def read_pkts(path, max_pkts=20000):
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
            out.append((ts_sec + ts_usec * 1e-6, data[14 + ihl + 8:]))
    return out

def parse(pl):
    pts = []
    for i in range(N):
        o = H + i * P
        v1, v2 = struct.unpack_from('<HH', pl, o)
        amp = pl[o + 4]
        cnt = struct.unpack_from('<H', pl, o + 5)[0]
        pts.append((v1, v2, amp, cnt))
    return pts

def main(path):
    pkts = read_pkts(path)
    print(f'packets: {len(pkts)}')

    prev_last = None
    n_valid = 0
    az_diffs, rng_diffs = [], []
    az_all, rng_all, amp_all, cnt_all = [], [], [], []
    wrap = 0
    prev_cnt = None
    boundary_diffs = []
    for pi, (ts, pl) in enumerate(pkts[:4000]):
        pts = parse(pl)
        valid = [p for p in pts if p[0] != 0 or p[1] != 0]
        az = np.array([p[0] for p in valid], dtype=np.int64)
        rng = np.array([p[1] for p in valid], dtype=np.int64)
        amp = np.array([p[2] for p in valid])
        cnt = np.array([p[3] for p in valid], dtype=np.int64)
        if len(valid) == 0:
            continue
        n_valid += len(valid)
        az_all.append(az); rng_all.append(rng); amp_all.append(amp); cnt_all.append(cnt)
        # within-packet adjacent diffs
        if len(az) > 1:
            d = az[1:] - az[:-1]
            d = np.minimum(d, 65536 - d)
            az_diffs.append(np.abs(d))
            d2 = rng[1:] - rng[:-1]
            d2 = np.minimum(d2, 65536 - d2)
            rng_diffs.append(np.abs(d2))
        # counter continuity
        for c in cnt:
            if prev_cnt is not None and c != prev_cnt:
                delta = c - prev_cnt if c >= prev_cnt else c + 65536 - prev_cnt
                if delta > 100:  # not +1? report
                    print(f'  !! cnt jump pkt{pi}: {prev_cnt} -> {c} (delta={delta})')
            prev_cnt = c
        # boundary: first valid point of this packet vs last of previous
        if prev_last is not None:
            d = az[0] - prev_last
            d = min(abs(d), 65536 - abs(d))
            boundary_diffs.append(d)
        prev_last = az[-1]

    az_all = np.concatenate(az_all); rng_all = np.concatenate(rng_all)
    amp_all = np.concatenate(amp_all); cnt_all = np.concatenate(cnt_all)
    az_d = np.concatenate(az_diffs) if az_diffs else np.array([0])
    rng_d = np.concatenate(rng_diffs) if rng_diffs else np.array([0])

    print(f'valid points: {n_valid}')
    print(f'az  range: [{az_all.min()}, {az_all.max()}]  median intra-pkt |daz|={np.median(az_d):.1f}  p95={np.percentile(az_d,95):.1f}')
    print(f'rng range: [{rng_all.min()}, {rng_all.max()}]  median intra-pkt |drng|={np.median(rng_d):.1f}  p95={np.percentile(rng_d,95):.1f}')
    print(f'amp range: [{amp_all.min()}, {amp_all.max()}]  mean={amp_all.mean():.1f}')
    print(f'cnt range: [{cnt_all.min()}, {cnt_all.max()}]  (wrap check)')
    print(f'packet-boundary |daz|: median={np.median(boundary_diffs):.1f}  p95={np.percentile(boundary_diffs,95):.1f}  (of {len(boundary_diffs)} boundaries)')

    # az histogram coarse: does it cover full circle?
    hist, edges = np.histogram(az_all, bins=16)
    print('az 16-bin histogram:', hist.tolist())

    # relationship az vs rng in one frame: is rng monotonic in az for a wall?
    # sample a dense packet
    for ts, pl in pkts[:4000]:
        pts = parse(pl)
        valid = [(p[0], p[1], p[2]) for p in pts if p[0] != 0 or p[1] != 0]
        if len(valid) > 50:
            az = np.array([p[0] for p in valid]); rng = np.array([p[1] for p in valid])
            order = np.argsort(az)
            print(f'\nsample dense pkt ts={ts:.3f} n={len(valid)}:')
            print('  az sorted first 12:', az[order][:12].tolist())
            print('  rng at those az   :', rng[order][:12].tolist())
            print('  amp histogram:', np.histogram([p[2] for p in valid], bins=8, range=(0, 255))[0].tolist())
            break

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')
