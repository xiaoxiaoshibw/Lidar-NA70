# -*- coding: utf-8 -*-
"""ASCII density visualization of a frame BEV to sanity-check geometry without image tools."""
import sys
import numpy as np
from pcap_reader import iter_packets
from radar_protocol import points_from_payload

CHARS = ' .:-=+*#%@'


def ascii_map(pts, span=20.0, res=0.5, rows=24, title=''):
    arr = np.array(pts)
    if len(arr) == 0:
        print('(no points)')
        return
    x = arr[:, 0]; y = arr[:, 1]
    cols = int(rows * 2)
    grid = np.zeros((rows, cols), dtype=np.int32)
    half = span
    j = ((x + half) / (2 * half) * (cols - 1)).astype(int)
    i = ((y + half) / (2 * half) * (rows - 1)).astype(int)
    m = (i >= 0) & (i < rows) & (j >= 0) & (j < cols)
    np.add.at(grid, (i[m], j[m]), 1)
    mx = grid.max() if grid.max() > 0 else 1
    print(f'--- {title}  (n={len(arr)}, x[{x.min():.1f},{x.max():.1f}] y[{y.min():.1f},{y.max():.1f}]) ---')
    for r in range(rows - 1, -1, -1):
        line = ''.join(CHARS[min(int(grid[r, c] / mx * (len(CHARS) - 1)), len(CHARS) - 1)] for c in range(cols))
        print(f'{span - r * (2 * span) / (rows - 1):6.1f} |{line}|')
    print('       ' + ''.join(str(c % 10) for c in range(cols)))


def main(path, n_frames=4, frame_s=1.0, start_offset_s=0.0):
    frames = []
    cur = []
    cur_start = None
    skip = True
    for ts, pl in iter_packets(path):
        if start_offset_s and skip:
            if cur_start is None:
                cur_start = ts
            if ts - cur_start < start_offset_s:
                continue
            skip = False
            cur_start = ts
            cur = []
        if cur_start is None:
            cur_start = ts
        pts = points_from_payload(pl)
        if pts:
            cur.append(pts)
        if ts - cur_start >= frame_s:
            flat = [p for ps in cur for p in ps]
            frames.append((cur_start, flat))
            if len(frames) >= n_frames:
                break
            cur = []
            cur_start = ts

    for i, (t, pts) in enumerate(frames):
        ascii_map(pts, title=f'frame {i} t={t:.3f}')


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap'
    off = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    main(path, start_offset_s=off)
