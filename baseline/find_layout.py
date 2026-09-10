# -*- coding: utf-8 -*-
"""Auto-search the true payload layout: header size H, point size P.
Criterion: parsed u16 fields should be locally smooth (radar scans are continuous).
Also try byte-autocorrelation for periodicity."""
import struct
import sys
import numpy as np

def read_dense(path, want=60):
    out = []
    with open(path, 'rb') as f:
        f.read(24)
        while len(out) < want:
            hdr = f.read(16)
            if len(hdr) < 16:
                break
            ts_sec, ts_usec, incl, orig = struct.unpack('<IIII', hdr)
            data = f.read(incl)
            ihl = (data[14] & 0x0F) * 4
            pl = data[14 + ihl + 8:]
            if sum(1 for b in pl if b) > 400:  # dense
                out.append(pl)
    return out

def autocorr_period(pl, maxlag=20):
    a = np.frombuffer(pl, dtype=np.uint8).astype(np.int16)
    n = len(a)
    best = []
    for lag in range(1, maxlag + 1):
        if lag >= n: break
        diff = np.abs(np.diff(a[::lag])).mean() if False else None
        # correlation of a[0:n-lag] and a[lag:n]
        x = a[:n - lag]; y = a[lag:]
        corr = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 else 0.0
        best.append((corr, lag))
    best.sort(reverse=True)
    return best[:6]

def smoothness(seq):
    """median abs diff of consecutive valid values (0 excluded)"""
    s = np.array(seq, dtype=np.int64)
    s = s[s != 0]
    if len(s) < 4:
        return 1e9
    d = np.abs(np.diff(s))
    # small is smoother; penalize huge jumps but allow wraparound (65536)
    d = np.minimum(d, 65536 - d)
    return float(np.median(d))

def try_layouts(pl):
    L = len(pl)
    results = []
    for H in range(0, 24):
        for P in range(3, 11):
            rem = L - H
            if rem <= 0 or rem % P != 0:
                continue
            N = rem // P
            # extract u16 (le) at offset 0 and 2 of each point if P>=4
            if P >= 4:
                v1, v2 = [], []
                for i in range(N):
                    o = H + i * P
                    v1.append(struct.unpack_from('<H', pl, o)[0])
                    v2.append(struct.unpack_from('<H', pl, o + 2)[0])
                sc = smoothness(v1) + smoothness(v2)
                results.append((sc, H, P, N, float(np.median(v1)), float(np.median(v2))))
    results.sort()
    return results[:10]

def main(path):
    pkts = read_dense(path)
    print(f'dense packets: {len(pkts)}')
    pl = pkts[3]
    print('payload len:', len(pl))
    print('autocorr top lags:', autocorr_period(pl))
    print('\nBest (H, P) by smoothness (lower=better):')
    for sc, H, P, N, m1, m2 in try_layouts(pl)[:8]:
        print(f'  H={H:2d} P={P} N={N:3d} score={sc:8.1f} median_v1={m1:.0f} median_v2={m2:.0f}')

    # for the best layout, dump first 12 points
    best = try_layouts(pl)[0]
    _, H, P, N, _, _ = best
    print(f'\nBest layout H={H} P={P} N={N}. First 14 points:')
    for i in range(min(14, N)):
        o = H + i * P
        if P == 5:
            v1, v2 = struct.unpack_from('<HH', pl, o)
            print(f'  [{i:3d}] v1={v1:6d} v2={v2:6d} v3={pl[o+4]:3d}')
        else:
            print(f'  [{i:3d}] bytes={pl[o:o+P].hex()}')

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')
