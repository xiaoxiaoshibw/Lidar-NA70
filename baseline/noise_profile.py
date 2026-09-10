# -*- coding: utf-8 -*-
"""Quantify frame-to-frame noise on REAL 1s frames to pick amp_thr."""
import sys
import numpy as np
from pcap_reader import iter_packets
from radar_protocol import points_from_payload
from run_baseline import build_grid

def main(path, frame_s=1.0, n_frames=8):
    grids = []
    cur = []
    cur_start = None
    for ts, pl in iter_packets(path):
        if cur_start is None:
            cur_start = ts
        pts = points_from_payload(pl)
        if pts:
            cur.append(pts)
        if ts - cur_start >= frame_s:
            grids.append(build_grid([p for ps in cur for p in ps]))
            if len(grids) >= n_frames:
                break
            cur = []
            cur_start = ts
    print(f'frames: {len(grids)}')
    for a in range(len(grids) - 1):
        A, B = grids[a], grids[a + 1]
        both = (A.touched > 0) & (B.touched > 0)
        d = np.abs(A.amp[both].astype(np.float32) - B.amp[both].astype(np.float32))
        print(f'frame {a}->{a+1}: A_occ={int((A.occ>0).sum())} B_occ={int((B.occ>0).sum())} '
              f'both={int(both.sum())}')
        if d.size:
            print(f'  amp_delta median={np.median(d):.1f} p90={np.percentile(d,90):.1f} '
                  f'p95={np.percentile(d,95):.1f} p99={np.percentile(d,99):.1f} max={d.max():.1f}')
        # new_region scale: B occ cells not in A touched
        newcells = int(((B.occ > 0) & (A.touched == 0)).sum())
        print(f'  new-region cells (B occ not in A): {newcells}')

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')
