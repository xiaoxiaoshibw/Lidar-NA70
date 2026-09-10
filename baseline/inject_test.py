# -*- coding: utf-8 -*-
"""Synthetic-injection test: inject a moving target into real pcap frames and verify
the baseline detector finds it. Proves pipeline (geometry/framediff/cluster/track)."""
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from pcap_reader import iter_packets
from radar_protocol import points_from_payload
from run_baseline import build_grid, frame_diff2, cluster, Tracker, track_speed


def load_frames(path, max_seconds=10.0, frame_s=1.0):
    frames = []
    cur = []
    cur_start = None
    session_start = None
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
    return frames


def inject_target(frames, start=(3.0, -1.0), end=(7.0, -1.0), speed=0.4,
                  n_per_frame=40, amp=(50, 90), sigma=0.2, seed=7):
    """Add a Gaussian blob target moving from start to end over the frame span."""
    rng = np.random.default_rng(seed)
    t0 = frames[0][0]
    t1 = frames[-1][0]
    dist = np.hypot(end[0] - start[0], end[1] - start[1])
    for t, pts in frames:
        frac = min((t - t0) / max(t1 - t0, 1e-6), 1.0)
        cx = start[0] + (end[0] - start[0]) * frac
        cy = start[1] + (end[1] - start[1]) * frac
        xs = cx + rng.normal(0, sigma, n_per_frame)
        ys = cy + rng.normal(0, sigma, n_per_frame)
        amps = rng.uniform(amp[0], amp[1], n_per_frame)
        az = np.zeros(n_per_frame); rngm = np.zeros(n_per_frame); ch = np.zeros(n_per_frame)
        new_pts = np.stack([xs, ys, amps, az, rngm, ch], axis=1)
        frames_final.append((t, np.vstack([pts, new_pts]) if len(pts) else new_pts))
    return dist / max(t1 - t0, 1e-6)


frames_final = []


def run_injection(path, out_dir=r'D:\Code\radar\baseline'):
    global frames_final
    frames = load_frames(path)
    expected_v = inject_target(frames)
    print(f'frames: {len(frames)}, injected target v={expected_v:.2f} m/s')

    trk = Tracker()
    results = []
    prev = None
    gt_pos = [(3.0 + (7.0 - 3.0) * min((t - frames_final[0][0]) / max(frames_final[-1][0] - frames_final[0][0], 1e-6), 1.0), -1.0)
              for t, _ in frames_final]
    for i, (t, pts) in enumerate(frames_final):
        g = build_grid(pts)
        cls = []
        if prev is not None:
            world, changed = frame_diff2(prev, g)
            cls = cluster(world)
            if i < 12:
                # how many changed cells near the injected target (within 1.5m)?
                gx, gy = gt_pos[i]
                near = world[(np.abs(world[:, 0] - gx) < 1.5) & (np.abs(world[:, 1] - gy) < 1.5)]
                print(f'  frame {i}: changed={changed.sum()} near_gt={len(near)} '
                      f'gt=({gx:.2f},{gy:.2f}) centroids='
                      f'{[ (round(c.mean(axis=0)[0],2), round(c.mean(axis=0)[1],2)) for c in cls ]}')
        trk.update(t, cls)
        results.append((t, len(pts), int(changed.sum()) if prev is not None else 0))
        prev = g

    moving = []
    for tid, hist in trk.tracks.items():
        spd, moved = track_speed(hist, min_frames=3)
        if moved and spd >= 0.3:
            moving.append((tid, hist, spd))
    moving.sort(key=lambda x: -x[2])
    print(f'\nmoving tracks found: {len(moving)}')
    for tid, hist, spd in moving:
        x0, y0 = hist[0][1], hist[0][2]
        x1, y1 = hist[-1][1], hist[-1][2]
        print(f'  track {tid}: {len(hist)} frames, ({x0:.2f},{y0:.2f})->({x1:.2f},{y1:.2f}), v={spd:.2f} m/s')

    ok = len(moving) >= 1
    print('INJECTION TEST:', 'PASS - detector found the moving target' if ok
          else 'FAIL - detector missed the injected target')

    # figure
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    t0, pts0 = frames_final[0]
    ax = axes[0]
    arr = np.array(pts0)
    ax.scatter(arr[:, 0], arr[:, 1], s=2, c='#ccc', alpha=0.4)
    for tid, hist, spd in moving:
        xs = [h[1] for h in hist]; ys = [h[2] for h in hist]
        ax.plot(xs, ys, '-o', ms=6, c='#EA6668', lw=2, label=f'v={spd:.2f} m/s')
    # ground truth line
    ax.plot([3, 8], [-1, -1], '--', c='#52C41A', lw=1.5, label='GT path')
    ax.set_title('Injected target (GT) vs detected track')
    ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)'); ax.set_aspect('equal'); ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    ax2 = axes[1]
    ts = [r[0] - frames_final[0][0] for r in results]
    cc = [r[2] for r in results]
    ax2.plot(ts, cc, lw=1)
    ax2.set_title('Changed cells per frame'); ax2.set_xlabel('time (s)'); ax2.set_ylabel('cells')
    plt.tight_layout()
    png = out_dir + r'\injection_test.png'
    plt.savefig(png, dpi=110)
    print('figure:', png)
    return ok


if __name__ == '__main__':
    p = sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap'
    run_injection(p)
