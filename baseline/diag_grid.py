# -*- coding: utf-8 -*-
"""Diagnose grid coverage per frame."""
import sys
import numpy as np
from pcap_reader import iter_packets
from radar_protocol import points_from_payload
from bev import BevGrid

def main(path):
    for fi, (ts, pl) in enumerate(iter_packets(path)):
        pts = points_from_payload(pl)
        if fi >= 4:
            break
        g = BevGrid(20.0, 0.1)
        if pts:
            g.add_points(np.asarray(pts, dtype=np.float32)[:, :3], use_amp=True)
        print(f'frame {fi}: pts={len(pts) if pts else 0} '
              f'occ_cells={g.occ.sum()} touched_cells={g.touched.sum()} '
              f'amp_nz={(g.amp>0).sum()} occ_nz={(g.occ>0).sum()}')
        # where are points? x,y histogram coarse
        if pts:
            arr = np.asarray(pts)
            print(f'  x[{arr[:,0].min():.2f},{arr[:,0].max():.2f}] y[{arr[:,1].min():.2f},{arr[:,1].max():.2f}]')

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')
