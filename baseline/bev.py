# -*- coding: utf-8 -*-
"""BEV occupancy grid + frame differencing + clustering (DBSCAN) + speed gating."""
import numpy as np
from sklearn.cluster import DBSCAN


class BevGrid:
    """Bird's-eye-view occupancy/amplitude grid in meters (radar frame at origin)."""

    def __init__(self, span=20.0, res=0.1):
        self.span = span          # +/- meters
        self.res = res            # meters per cell
        self.n = int(round(2 * span / res))
        self.occ = np.zeros((self.n, self.n), dtype=np.uint8)
        self.amp = np.zeros((self.n, self.n), dtype=np.float32)
        self.touched = np.zeros((self.n, self.n), dtype=np.uint8)

    def reset(self):
        self.occ.fill(0)
        self.amp.fill(0.0)
        self.touched.fill(0)

    def _idx(self, x, y):
        i = ((y + self.span) / self.res).astype(np.int32)
        j = ((x + self.span) / self.res).astype(np.int32)
        return i, j

    def add_points(self, pts, use_amp=False):
        """pts: array-like of (x, y[, amp]). Bins outside span are dropped."""
        arr = np.asarray(pts, dtype=np.float32)
        if arr.ndim == 1 or arr.size == 0:
            return
        if arr.ndim == 2 and arr.shape[1] < 2:
            return
        x = arr[:, 0]
        y = arr[:, 1]
        amp = arr[:, 2] if arr.shape[1] > 2 and use_amp else np.ones(len(x), dtype=np.float32)
        i = ((y + self.span) / self.res).astype(np.int32)
        j = ((x + self.span) / self.res).astype(np.int32)
        m = (i >= 0) & (i < self.n) & (j >= 0) & (j < self.n)
        i, j, amp = i[m], j[m], amp[m]
        if len(i) == 0:
            return
        np.add.at(self.occ, (i, j), 1)
        # max-amplitude accumulator
        cur = self.amp[i, j]
        upd = amp > cur
        if upd.any():
            self.amp[i[upd], j[upd]] = amp[upd]
        self.touched[i, j] = 1


def frame_diff(prev: BevGrid, cur: BevGrid, amp_thr=8.0, occ_thr=0):
    """Return changed-cell mask (world coords of changed cells) between two full-scan frames.
    A cell 'changed' if amplitude dropped/rose beyond threshold, or occupancy flipped
    between 0 and >=1 (only where at least one frame sampled the cell)."""
    both = (prev.touched > 0) | (cur.touched > 0)
    amp_delta = np.abs(prev.amp.astype(np.float32) - cur.amp.astype(np.float32))
    # occupancy state flip with strict threshold
    p_occ = (prev.occ > occ_thr).astype(np.int8)
    c_occ = (cur.occ > occ_thr).astype(np.int8)
    flip = (p_occ != c_occ).astype(np.int8)
    changed = both & ((amp_delta >= amp_thr) | (flip == 1))
    ys, xs = np.nonzero(changed)
    n = cur.n
    half = cur.span
    res = cur.res
    world = np.stack([xs * res - half + res / 2, ys * res - half + res / 2], axis=1)
    return world, changed


def cluster_moving(world_pts, eps=0.6, min_samples=4):
    """DBSCAN on changed-cell world coordinates. Returns list of clusters (arrays)."""
    if world_pts is None or len(world_pts) < min_samples:
        return []
    lab = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(world_pts)
    clusters = []
    for k in np.unique(lab):
        if k == -1:
            continue
        m = lab == k
        if m.sum() >= min_samples:
            clusters.append(world_pts[m])
    return clusters
