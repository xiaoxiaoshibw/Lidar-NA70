"""Sliding-window point-cloud accumulation (LiDAR-only, no IMU).

Static-sensor mode (situation A): the window is merged, range/ROI gated and
voxel downsampled.  A registration hook (``compensator``) is reserved for
situation B so scan-to-scan compensation can be plugged in later without
changing callers; this module never invents motion it cannot observe.
"""
from __future__ import annotations

import numpy as np

from .buffer import FrameBuffer, StampedCloud


class Accumulator:
    """Keeps a ``window_size`` ring buffer and rebuilds the accumulated cloud.

    The accumulated cloud is recomputed on every push so voxel occupancy always
    matches the exact window contents (no incremental state to drift).
    """

    def __init__(self, accumulation_cfg: dict, compensator=None):
        self.window_size = int(accumulation_cfg["window_size"])
        self.voxel_size = float(accumulation_cfg["voxel_size"])
        self.max_points = int(accumulation_cfg["max_points"])
        self.range_min = float(accumulation_cfg["range"]["min"])
        self.range_max = float(accumulation_cfg["range"]["max"])
        self.roi = accumulation_cfg.get("roi")
        self.enabled = bool(accumulation_cfg.get("enabled", True))
        self.compensator = compensator
        self.buffer = FrameBuffer(self.window_size)
        self._version = -1
        self._cache: tuple[np.ndarray, np.ndarray] = (
            np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.float32))

    def push(self, cloud: StampedCloud) -> dict:
        """Add one frame and return statistics for the refreshed window."""
        if not self.enabled:
            raise ValueError("accumulation.enabled 为 false 时不应调用 push")
        if self.compensator is not None:
            cloud = self.compensator(cloud)
        self.buffer.push(cloud)
        self._version += 1
        xyz, intensity = self.accumulated()
        return {
            "frame_index": cloud.frame_index,
            "accumulated_frames": len(self.buffer),
            "window_raw_points": int(sum(len(f.points) for f in self.buffer)),
            "accumulated_points": int(len(xyz)),
            "monotonic": self.buffer.monotonic,
        }

    def accumulated(self) -> tuple[np.ndarray, np.ndarray]:
        """Return the (xyz, intensity) of the current window."""
        if self._version == len(self.buffer.frames) and self.buffer.is_full():
            return self._cache
        clouds = self.buffer.window()
        if not clouds:
            result = (np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.float32))
        else:
            result = self._merge(clouds)
        self._version = len(self.buffer.frames)
        self._cache = result
        return result

    def _merge(self, clouds: list[StampedCloud]) -> tuple[np.ndarray, np.ndarray]:
        points = np.vstack([f.points for f in clouds]) if len(clouds) > 1 else clouds[0].points
        xyz = points[:, :3].astype(np.float64, copy=False)
        intensity = points[:, 3].astype(np.float64, copy=False)
        keep = np.isfinite(points).all(axis=1)
        distance = np.linalg.norm(xyz, axis=1) if len(xyz) else np.zeros(len(xyz))
        keep &= (distance >= self.range_min) & (distance <= self.range_max) & (distance > 0)
        if self.roi is not None:
            bounds = np.asarray(self.roi, dtype=np.float64).reshape(3, 2)
            keep &= np.all((xyz >= bounds[:, 0]) & (xyz <= bounds[:, 1]), axis=1)
        xyz, intensity = xyz[keep], intensity[keep]
        if len(xyz) == 0:
            return np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.float32)
        return _voxel_downsample(xyz, intensity, self.voxel_size, self.max_points)


def _voxel_downsample(xyz: np.ndarray, intensity: np.ndarray,
                      voxel: float, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """Voxel-centroid downsample; the centroid carries the mean intensity."""
    keys = np.floor(xyz / voxel).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    counts = np.bincount(inverse, minlength=len(unique)).astype(np.float64)
    sums = np.zeros((len(unique), 4), dtype=np.float64)
    stacked = np.column_stack((xyz, intensity))
    for column in range(4):
        sums[:, column] = np.bincount(inverse, weights=stacked[:, column], minlength=len(unique))
    centroids = sums / counts[:, None]
    if limit and len(centroids) > limit:
        centroids = centroids[np.linspace(0, len(centroids) - 1, int(limit), dtype=np.int64)]
    return centroids[:, :3].astype(np.float32), centroids[:, 3].astype(np.float32)
