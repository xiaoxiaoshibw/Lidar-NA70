# -*- coding: utf-8 -*-
"""MotionDetector: consumes frame point sets, emits per-frame detections."""
import numpy as np
from bev import BevGrid, frame_diff, cluster_moving


class MotionDetector:
    """Compare consecutive full-scan frames. Frame must cover a full 360 deg scan
    (or at least same sectors) for direct differencing to be valid."""

    def __init__(self, span=20.0, res=0.1, amp_thr=8.0,
                 eps=0.6, min_samples=4, speed_thr=0.3):
        self.span = span
        self.res = res
        self.amp_thr = amp_thr
        self.eps = eps
        self.min_samples = min_samples
        self.speed_thr = speed_thr
        self.prev_grid = None
        self.prev_clusters = None
        self.prev_t = None

    def process_frame(self, t_start, points):
        grid = BevGrid(self.span, self.res)
        if points:
            arr = np.array(points, dtype=np.float32)
            grid.add_points(arr[:, :3], use_amp=True)
        result = {
            't': t_start, 'n_points': len(points) if points else 0,
            'targets': [], 'moving': False, 'changed_cells': 0,
        }
        if self.prev_grid is None:
            self.prev_grid = grid
            self.prev_clusters = None
            self.prev_t = t_start
            return result

        world, changed = frame_diff(self.prev_grid, grid, amp_thr=self.amp_thr)
        result['changed_cells'] = int(changed.sum())
        clusters = cluster_moving(world, eps=self.eps, min_samples=self.min_samples)
        dt = max(t_start - self.prev_t, 1e-3)
        targets = []
        for cl in clusters:
            c = cl.mean(axis=0)
            area = float(len(cl))
            v = 0.0
            if self.prev_clusters:
                # nearest previous cluster centroid
                pc = np.array([pc.mean(axis=0) for pc in self.prev_clusters]) if self.prev_clusters else None
                if pc is not None and len(pc) > 0:
                    d = np.linalg.norm(pc - c, axis=1)
                    j = int(np.argmin(d))
                    if d[j] < 2.0:
                        v = d[j] / dt
            moving = v >= self.speed_thr
            targets.append({'x': float(c[0]), 'y': float(c[1]), 'area': area,
                            'speed': float(v), 'moving': bool(moving)})
            result['moving'] = result['moving'] or moving
        result['targets'] = targets
        self.prev_grid = grid
        self.prev_clusters = clusters
        self.prev_t = t_start
        return result
