# -*- coding: utf-8 -*-
"""Baseline v2: frame differencing with track continuity.
A target counts as MOVING only if it persists >= min_track frames and its
displacement over the track exceeds speed_thr * track_duration."""
import sys
import csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from pcap_reader import iter_packets
from radar_protocol import points_from_payload
from bev import BevGrid
from scipy import ndimage
from sklearn.cluster import DBSCAN


def build_grid(pts, span=20.0, res=0.1):
    g = BevGrid(span, res)
    g.add_points(np.asarray(pts, dtype=np.float32)[:, :3], use_amp=True)
    g.touched = ndimage.binary_dilation(g.touched > 0, iterations=1).astype(np.uint8)
    return g


def frame_diff2(gA, gB, amp_thr=30.0):
    both = (gA.touched > 0) & (gB.touched > 0)
    amp_delta = np.abs(gA.amp.astype(np.float32) - gB.amp.astype(np.float32))
    changed = both & (amp_delta >= amp_thr)
    new_region = (gA.touched == 0) & (gB.occ > 0)
    changed = changed | new_region
    # keep connected components with >=4 cells (sparse radar targets survive);
    # drop scattered static noise
    lbl, n = ndimage.label(changed)
    if n > 0:
        sizes = ndimage.sum(changed, lbl, range(1, n + 1))
        keep = sizes >= 4
        changed = np.isin(lbl, np.nonzero(keep)[0] + 1)
    else:
        changed = changed & False
    ys, xs = np.nonzero(changed)
    if len(xs) == 0:
        return np.empty((0, 2)), changed
    half = gB.span
    res = gB.res
    world = np.stack([xs * res - half + res / 2, ys * res - half + res / 2], axis=1)
    return world, changed


def cluster(world, eps=0.9, min_samples=4):
    if len(world) < min_samples:
        return []
    lab = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(world)
    out = []
    for k in np.unique(lab):
        if k == -1:
            continue
        m = lab == k
        if m.sum() >= min_samples:
            out.append(world[m])
    return out


class Tracker:
    """Tiny nearest-neighbour tracker for cluster centroids."""

    def __init__(self, match_dist=2.0):
        self.match_dist = match_dist
        self.tracks = {}   # id -> list of (t, x, y)

    def update(self, t, clusters):
        next_tracks = {}
        used = set()
        # match new clusters to existing tracks (nearest first)
        for tid, hist in self.tracks.items():
            last = hist[-1]
            best = None
            for j, cl in enumerate(clusters):
                if j in used:
                    continue
                c = cl.mean(axis=0)
                d = np.hypot(c[0] - last[1], c[1] - last[2])
                if best is None or d < best[0]:
                    best = (d, j)
            if best and best[0] <= self.match_dist:
                used.add(best[1])
                c = clusters[best[1]].mean(axis=0)
                next_tracks[tid] = hist + [(t, c[0], c[1])]
        # new tracks
        tid_new = max(self.tracks.keys(), default=0) + 1
        for j, cl in enumerate(clusters):
            if j in used:
                continue
            c = cl.mean(axis=0)
            next_tracks[tid_new] = [(t, c[0], c[1])]
            tid_new += 1
        self.tracks = next_tracks
        return self.tracks


def track_speed(hist, min_frames=3):
    """Return (speed_mps, moved) if track length >= min_frames and moved distance / dt."""
    if len(hist) < min_frames:
        return 0.0, False
    t0 = hist[0][0]; t1 = hist[-1][0]
    x0, y0 = hist[0][1], hist[0][2]
    x1, y1 = hist[-1][1], hist[-1][2]
    dt = max(t1 - t0, 0.1)
    d = np.hypot(x1 - x0, y1 - y0)
    return d / dt, d > 0.01


def main(path, max_seconds=60.0, frame_s=1.0, out_dir=r'D:\Code\radar\baseline',
         speed_thr=0.3, min_track=3):
    print(f'== pcap: {path} ==')
    frames = []
    cur_start = None
    session_start = None
    cur = []
    for ts, pl in iter_packets(path):
        if session_start is None:
            session_start = ts
            cur_start = ts
        if ts - session_start > max_seconds:
            break
        pts = points_from_payload(pl)
        if pts:
            cur.append(pts)
        if ts - cur_start >= frame_s:
            frames.append((cur_start, [p for ps in cur for p in ps]))
            cur = []
            cur_start = ts
    if cur:
        frames.append((cur_start, [p for ps in cur for p in ps]))
    print(f'frames: {len(frames)}')

    trk = Tracker()
    results = []
    prev = None
    for t, pts in frames:
        g = build_grid(pts)
        res = {'t': t, 'n_points': len(pts), 'changed_cells': 0, 'clusters': [], 'tracks': []}
        cls = []
        if prev is not None:
            world, changed = frame_diff2(prev, g)
            res['changed_cells'] = int(changed.sum())
            cls = cluster(world)
        res['clusters'] = cls
        trks = trk.update(t, cls)
        res['tracks'] = trks
        results.append(res)
        prev = g

    # ---- per-track stats ----
    moving_tracks = []
    for tid, hist in trk.tracks.items():
        spd, moved = track_speed(hist, min_track)
        if moved and spd >= speed_thr:
            moving_tracks.append((tid, hist, spd))
    moving_tracks.sort(key=lambda x: -x[2])

    frames_with_moving = set()
    for tid, hist, spd in moving_tracks:
        for h in hist:
            frames_with_moving.add(h[0])

    n_moving = len(moving_tracks)
    print(f'\n== SUMMARY ==')
    print(f'frames analyzed       : {len(results)}')
    print(f'moving tracks (persist>={min_track}f, v>={speed_thr} m/s): {n_moving}')
    for tid, hist, spd in moving_tracks:
        t0 = hist[0][0] - results[0]['t']
        t1 = hist[-1][0] - results[0]['t']
        x0, y0 = hist[0][1], hist[0][2]
        x1, y1 = hist[-1][1], hist[-1][2]
        print(f'  track {tid}: {len(hist)} frames, t=[{t0:.1f},{t1:.1f}]s, '
              f'({x0:.1f},{y0:.1f})->({x1:.1f},{y1:.1f}), v={spd:.2f} m/s')
    print('=> MOVING OBJECTS DETECTED' if n_moving else '=> no moving objects')

    # ---- csv ----
    base = path.replace('\\', '/').split('/')[-1]
    if base.lower().endswith('.pcap'):
        base = base[:-5]
    csv_path = out_dir + f'\\detections_{base}.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['track_id', 'frame_t', 'x_m', 'y_m', 'speed_mps', 'moving'])
        for tid, hist, spd in moving_tracks:
            for (t, x, y) in hist:
                w.writerow([tid, f'{t:.6f}', f'{x:.3f}', f'{y:.3f}', f'{spd:.3f}', 1])
        # also dump all tracks for reference
        for tid, hist in trk.tracks.items():
            spd, moved = track_speed(hist, min_track)
            if moved and spd >= speed_thr:
                continue
            for (t, x, y) in hist:
                w.writerow([tid, f'{t:.6f}', f'{x:.3f}', f'{y:.3f}', f'{spd:.3f}', 0])
    print('csv:', csv_path)

    make_figs(frames, results, moving_tracks, out_dir, base)
    return n_moving


def make_figs(frames, results, moving_tracks, out_dir, base='baseline'):
    fig, axes = plt.subplots(2, 2, figsize=(14, 12))
    t0, pts0 = frames[0]
    ax = axes[0, 0]
    if pts0:
        arr = np.array(pts0)
        sc = ax.scatter(arr[:, 0], arr[:, 1], s=2, c=arr[:, 2], cmap='viridis', alpha=0.8)
        plt.colorbar(sc, ax=ax, label='amp')
    ax.set_title(f'Raw BEV frame 0 ({len(pts0)} pts)')
    ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)'); ax.set_aspect('equal'); ax.grid(alpha=0.3)

    ax = axes[0, 1]
    if pts0:
        arr = np.array(pts0)
        ax.scatter(arr[:, 0], arr[:, 1], s=2, c='#ccc', alpha=0.4)
    for tid, hist, spd in moving_tracks:
        xs = [h[1] for h in hist]; ys = [h[2] for h in hist]
        ax.plot(xs, ys, '-o', ms=5, lw=1.5, label=f'track {tid} v={spd:.2f}')
    ax.set_title('Moving tracks overlaid on BEV')
    ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)'); ax.set_aspect('equal'); ax.grid(alpha=0.3)
    if moving_tracks:
        ax.legend(fontsize=8)

    ax = axes[1, 0]
    ts = [r['t'] - frames[0][0] for r in results]
    cc = [r['changed_cells'] for r in results]
    ax.plot(ts, cc, lw=1)
    ax.set_title('Changed cells per frame'); ax.set_xlabel('time (s)'); ax.set_ylabel('cells')

    ax = axes[1, 1]
    for tid, hist, spd in moving_tracks:
        ts_ = [h[0] - frames[0][0] for h in hist]
        ax.plot(ts_, [spd] * len(ts_), 'o-', lw=1, ms=4)
    ax.axhline(0.3, color='#FAAD14', ls='--', lw=1, label='thr')
    ax.set_title('Moving track speeds'); ax.set_xlabel('time (s)'); ax.set_ylabel('speed (m/s)')
    ax.legend(fontsize=7)
    plt.tight_layout()
    png = out_dir + f'\\baseline_result_{base}.png'
    plt.savefig(png, dpi=110)
    print('figure:', png)


if __name__ == '__main__':
    p = sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap'
    max_sec = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    main(p, max_seconds=max_sec)
