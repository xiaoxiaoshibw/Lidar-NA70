# -*- coding: utf-8 -*-
"""Analyze radar UDP payload: try 5-byte point structure (u16,u16,u8).
Stats: value ranges, per-packet point count, smoothness, field correlation."""
import struct
import sys
import numpy as np

def read_payloads(path, max_pkts=200000):
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
            udp = 14 + ihl
            out.append((ts_sec + ts_usec * 1e-6, data[udp + 8:]))
    return out

def parse_points(payload):
    """Assume 5-byte points (u16,u16,u8). Return list of (v1,v2,v3) or None."""
    if len(payload) % 5 != 0:
        return None
    pts = []
    for i in range(0, len(payload), 5):
        v1, v2 = struct.unpack('<HH', payload[i:i+4])
        v3 = payload[i+4]
        if v1 == 0 and v2 == 0 and v3 == 0:
            continue  # invalid point
        pts.append((v1, v2, v3))
    return pts

def main(path):
    pkts = read_payloads(path)
    print(f'Total packets read: {len(pkts)}')
    nonzero = [p for p in pkts if any(p[1])]
    print(f'Nonzero payload packets: {len(nonzero)} ({100*len(nonzero)/len(pkts):.1f}%)')

    npts_list = []
    v1_all, v2_all, v3_all = [], [], []
    sample_pkts = []
    for ts, pl in nonzero[:3000]:
        pts = parse_points(pl)
        if pts is None:
            print('  !! payload len not multiple of 5:', len(pl))
            continue
        npts_list.append(len(pts))
        v1_all += [p[0] for p in pts]
        v2_all += [p[1] for p in pts]
        v3_all += [p[2] for p in pts]
        if len(sample_pkts) < 5:
            sample_pkts.append((ts, pts[:20]))

    print(f'Points per packet: min={min(npts_list)} max={max(npts_list)} avg={np.mean(npts_list):.1f}')
    v1 = np.array(v1_all); v2 = np.array(v2_all); v3 = np.array(v3_all)
    print(f'v1 (u16): min={v1.min()} max={v1.max()} mean={v1.mean():.1f}')
    print(f'v2 (u16): min={v2.min()} max={v2.max()} mean={v2.mean():.1f}')
    print(f'v3 (u8) : min={v3.min()} max={v3.max()} mean={v3.mean():.1f}')
    # check if v3 looks like intensity (few discrete values?)
    print('v3 unique values (top 12):', np.bincount(v3).argsort()[-12:][::-1].tolist(), '->', np.bincount(v3)[np.bincount(v3).argsort()[-12:][::-1]].tolist())

    # smoothness: within one packet, are v1/v2 monotonic?
    for ts, pts in sample_pkts:
        vs1 = [p[0] for p in pts]; vs2 = [p[1] for p in pts]
        d1 = np.diff(vs1); d2 = np.diff(vs2)
        print(f'sample ts={ts:.3f}: first 20 pts v1={vs1[:8]}... v2={vs2[:8]}... '
              f'dv1 range [{d1.min()},{d1.max()}] dv2 range [{d2.min()},{d2.max()}]')

    # histogram of v1 for one packet (is v1 angle in 0.1deg? range?)
    ts, pts = sample_pkts[0]
    vs1 = np.array([p[0] for p in pts]); vs2 = np.array([p[1] for p in pts])
    print(f'\nPacket ts={ts:.3f} has {len(pts)} points')
    print('v1 percentile:', np.percentile(vs1, [0, 25, 50, 75, 100]).round(1))
    print('v2 percentile:', np.percentile(vs2, [0, 25, 50, 75, 100]).round(1))
    # spacing between sorted unique v2 (angle bins?)
    su2 = np.unique(vs2)
    print('unique v2 count:', len(su2), 'spacing min/median/max:', np.diff(su2).min() if len(su2)>1 else '-',
          np.median(np.diff(su2)) if len(su2)>1 else '-', np.diff(su2).max() if len(su2)>1 else '-')

    # save stats for plotting
    np.save(r'D:\Code\radar\baseline\_v1v2_analysis.npy',
            {'v1': v1[:200000], 'v2': v2[:200000], 'v3': v3[:200000]}, allow_pickle=True)
    print('saved _v1v2_analysis.npy')

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')
