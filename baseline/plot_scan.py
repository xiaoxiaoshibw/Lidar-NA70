# -*- coding: utf-8 -*-
"""Plot (v1,v2) and (v1,v3) scatter for consecutive packets to reveal geometry."""
import struct
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

H, P, N = 10, 7, 200

def read_pkts(path, max_pkts=6000):
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
        v1, v2, v3 = struct.unpack_from('<HHH', pl, o)
        v4 = pl[o + 6]
        pts.append((v1, v2, v3, v4))
    return pts

def main(path):
    pkts = read_pkts(path)
    dense = [(ts, pl) for ts, pl in pkts if sum(1 for b in pl if b) > 400]
    print(f'dense packets: {len(dense)}')

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    colors = plt.cm.viridis(np.linspace(0, 1, 6))

    # 1) v1 vs v2 for 6 consecutive dense packets
    ax = axes[0, 0]
    for k, (ts, pl) in enumerate(dense[:6]):
        pts = parse(pl)
        v1 = np.array([p[0] for p in pts]); v2 = np.array([p[1] for p in pts])
        mask = (v1 > 0) & (v2 > 0)
        ax.scatter(v1[mask], v2[mask], s=3, c=[colors[k]], alpha=0.7, label=f'pkt{k} {ts:.3f}')
    ax.set_title('v1 vs v2 (6 consecutive packets)')
    ax.set_xlabel('v1'); ax.set_ylabel('v2')
    ax.legend(fontsize=7)

    # 2) v1 vs v3 for 3 packets
    ax = axes[0, 1]
    for k in range(3):
        ts, pl = dense[k]
        pts = parse(pl)
        v1 = np.array([p[0] for p in pts]); v3 = np.array([p[2] for p in pts])
        mask = v1 > 0
        ax.scatter(v1[mask], v3[mask], s=3, c=[colors[k]], alpha=0.7, label=f'pkt{k}')
    ax.set_title('v1 vs v3 (3 packets)')
    ax.set_xlabel('v1'); ax.set_ylabel('v3')
    ax.legend(fontsize=7)

    # 3) histogram of v2 values (range?) for one packet
    ax = axes[1, 0]
    ts, pl = dense[0]
    pts = parse(pl)
    v2 = np.array([p[1] for p in pts]); v4 = np.array([p[3] for p in pts])
    for tval in sorted(set(v4)):
        m = v4 == tval
        ax.hist(v2[m], bins=50, alpha=0.5, label=f'v4={tval} (n={m.sum()})')
    ax.set_title(f'v2 histogram by v4 flag (pkt ts={ts:.3f})')
    ax.set_xlabel('v2'); ax.set_ylabel('count')
    ax.legend(fontsize=7)

    # 4) v1 as scan angle over packets: v1 values used by each packet
    ax = axes[1, 1]
    for k in range(0, 30, 5):
        ts, pl = dense[k]
        pts = parse(pl)
        v1 = np.array([p[0] for p in pts])
        m = v1 > 0
        ax.hist(v1[m], bins=40, alpha=0.5, label=f'pkt{k} ts={ts:.3f}')
    ax.set_title('v1 distribution per packet (scan position?)')
    ax.set_xlabel('v1'); ax.set_ylabel('count')
    ax.legend(fontsize=6)

    plt.tight_layout()
    out = r'D:\Code\radar\baseline\analysis_scan.png'
    plt.savefig(out, dpi=110)
    print('saved', out)

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')
