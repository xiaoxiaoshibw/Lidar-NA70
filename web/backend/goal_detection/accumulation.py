"""Sliding-window point-cloud accumulation (LiDAR-only, no IMU).

Static-sensor mode (situation A): the window is merged, range/ROI gated and
voxel downsampled.  A registration hook (``compensator``) is reserved for
situation B so scan-to-scan compensation can be plugged in later without
changing callers; this module never invents motion it cannot observe.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import numpy as np

from .buffer import FrameBuffer, StampedCloud


@dataclass(frozen=True)
class VoxelProvenance:
    """Compact CSR mapping from output voxel rows to original frame/point IDs."""

    offsets: np.ndarray
    frame_indices: np.ndarray
    point_indices: np.ndarray
    device_times: np.ndarray

    @classmethod
    def empty(cls) -> "VoxelProvenance":
        return cls(np.zeros(1, dtype=np.int64), np.empty(0, dtype=np.int64),
                   np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64))

    def sources_for(self, voxel_index: int) -> np.ndarray:
        index = int(voxel_index)
        if index < 0 or index + 1 >= len(self.offsets):
            raise IndexError("voxel_index 越界")
        lo, hi = int(self.offsets[index]), int(self.offsets[index + 1])
        return np.column_stack((self.frame_indices[lo:hi], self.point_indices[lo:hi]))

    def device_times_for(self, voxel_index: int) -> np.ndarray:
        """Return source device times aligned with :meth:`sources_for` rows.

        ``NaN`` means that the decoder did not provide a device timestamp for
        that source frame.  Keeping the array aligned makes age calculation
        deterministic without changing the long-standing two-column API.
        """
        index = int(voxel_index)
        if index < 0 or index + 1 >= len(self.offsets):
            raise IndexError("voxel_index 越界")
        lo, hi = int(self.offsets[index]), int(self.offsets[index + 1])
        return self.device_times[lo:hi]

    @property
    def output_count(self) -> int:
        return max(len(self.offsets) - 1, 0)


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
        self._push_count = 0
        self._cache_version = -1
        self._cache: tuple[np.ndarray, np.ndarray] = (
            np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.float32))
        self._provenance = VoxelProvenance.empty()
        self.last_source_refs = np.empty((0, 2), dtype=np.int64)
        self.last_filter_classes = np.empty(0, dtype=np.uint8)
        self.last_trace: list[dict] = []
        self.cloud_id = "acc:empty"

    def push(self, cloud: StampedCloud) -> dict:
        """Add one frame and return statistics for the refreshed window."""
        if not self.enabled:
            raise ValueError("accumulation.enabled 为 false 时不应调用 push")
        if self.compensator is not None:
            cloud = self.compensator(cloud)
        self.buffer.push(cloud)
        self._push_count += 1
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
        if self._cache_version == self._push_count:
            return self._cache
        clouds = self.buffer.window()
        if not clouds:
            result = (np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.float32))
        else:
            result = self._merge(clouds)
        self._cache_version = self._push_count
        self._cache = result
        return result

    @property
    def provenance(self) -> VoxelProvenance:
        # Ensure the cache and its mapping describe the same window.
        self.accumulated()
        return self._provenance

    def _merge(self, clouds: list[StampedCloud]) -> tuple[np.ndarray, np.ndarray]:
        started = perf_counter()
        points = np.vstack([f.points for f in clouds]) if len(clouds) > 1 else clouds[0].points
        frame_indices = np.concatenate([
            np.full(len(frame.points), frame.frame_index, dtype=np.int64) for frame in clouds])
        point_indices = np.concatenate([
            np.arange(len(frame.points), dtype=np.int64) for frame in clouds])
        device_times = np.concatenate([
            np.full(len(frame.points), np.nan if frame.device_time is None
                    else float(frame.device_time), dtype=np.float64)
            for frame in clouds])
        self.last_source_refs = np.column_stack((frame_indices, point_indices))
        xyz = points[:, :3].astype(np.float64, copy=False)
        intensity = points[:, 3].astype(np.float64, copy=False)
        finite = np.isfinite(points).all(axis=1)
        distance = np.linalg.norm(xyz, axis=1) if len(xyz) else np.zeros(len(xyz))
        in_range = ((distance >= self.range_min) & (distance <= self.range_max)
                    & (distance > 0))
        in_roi = np.ones(len(points), dtype=bool)
        if self.roi is not None:
            bounds = np.asarray(self.roi, dtype=np.float64).reshape(3, 2)
            in_roi = np.all((xyz >= bounds[:, 0]) & (xyz <= bounds[:, 1]), axis=1)
        keep = finite & in_range & in_roi
        classes = np.zeros(len(points), dtype=np.uint8)
        classes[~finite] = 1
        classes[finite & ~in_range] = 2
        classes[finite & in_range & ~in_roi] = 3
        self.last_filter_classes = classes
        filter_elapsed = (perf_counter() - started) * 1000.0
        xyz, intensity = xyz[keep], intensity[keep]
        kept_frames, kept_points = frame_indices[keep], point_indices[keep]
        if len(xyz) == 0:
            self._provenance = VoxelProvenance.empty()
            self.cloud_id = f"acc:{clouds[0].frame_index}-{clouds[-1].frame_index}:{self._push_count}"
            self.last_trace = [
                _trace("range_roi_filter", len(points), 0, filter_elapsed,
                       {"non_finite": int((classes == 1).sum()),
                        "outside_range": int((classes == 2).sum()),
                        "outside_roi": int((classes == 3).sum())}),
                _trace("voxel_downsample", 0, 0, None, {}, status="skipped"),
            ]
            return np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.float32)
        voxel_started = perf_counter()
        kept_times = device_times[keep]
        out_xyz, out_intensity, provenance = _voxel_downsample_with_provenance(
            xyz, intensity, kept_frames, kept_points,
            self.voxel_size, self.max_points, source_times=kept_times)
        self._provenance = provenance
        self.cloud_id = f"acc:{clouds[0].frame_index}-{clouds[-1].frame_index}:{self._push_count}"
        self.last_trace = [
            _trace("range_roi_filter", len(points), len(xyz), filter_elapsed,
                   {"non_finite": int((classes == 1).sum()),
                    "outside_range": int((classes == 2).sum()),
                    "outside_roi": int((classes == 3).sum())}),
            _trace("voxel_downsample", len(xyz), len(out_xyz),
                   (perf_counter() - voxel_started) * 1000.0,
                   {"aggregated_points": int(len(xyz) - len(out_xyz))},
                   aggregate_count=int(len(xyz))),
        ]
        return out_xyz, out_intensity


def _voxel_downsample(xyz: np.ndarray, intensity: np.ndarray,
                      voxel: float, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """Voxel-centroid downsample; the centroid carries the mean intensity."""
    count = len(xyz)
    frames = np.zeros(count, dtype=np.int64)
    points = np.arange(count, dtype=np.int64)
    out_xyz, out_intensity, _ = _voxel_downsample_with_provenance(
        xyz, intensity, frames, points, voxel, limit)
    return out_xyz, out_intensity


def _voxel_downsample_with_provenance(
        xyz: np.ndarray, intensity: np.ndarray, source_frames: np.ndarray,
        source_points: np.ndarray, voxel: float, limit: int,
        source_times: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, VoxelProvenance]:
    keys = np.floor(xyz / voxel).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    counts = np.bincount(inverse, minlength=len(unique)).astype(np.float64)
    sums = np.zeros((len(unique), 4), dtype=np.float64)
    stacked = np.column_stack((xyz, intensity))
    for column in range(4):
        sums[:, column] = np.bincount(inverse, weights=stacked[:, column], minlength=len(unique))
    centroids = sums / counts[:, None]
    selected = np.arange(len(centroids), dtype=np.int64)
    if limit and len(selected) > limit:
        selected = selected[np.linspace(0, len(selected) - 1, int(limit), dtype=np.int64)]
    centroids = centroids[selected]
    remap = np.full(len(unique), -1, dtype=np.int64)
    remap[selected] = np.arange(len(selected), dtype=np.int64)
    mapped = remap[inverse]
    source_keep = mapped >= 0
    kept_positions = np.flatnonzero(source_keep)
    order = np.argsort(mapped[source_keep], kind="stable")
    kept_positions = kept_positions[order]
    mapped_sorted = mapped[kept_positions]
    source_counts = np.bincount(mapped_sorted, minlength=len(selected))
    offsets = np.concatenate(([0], np.cumsum(source_counts))).astype(np.int64)
    provenance = VoxelProvenance(
        offsets=offsets,
        frame_indices=np.asarray(source_frames, dtype=np.int64)[kept_positions],
        point_indices=np.asarray(source_points, dtype=np.int64)[kept_positions],
        device_times=(np.full(len(source_frames), np.nan, dtype=np.float64)
                      if source_times is None else
                      np.asarray(source_times, dtype=np.float64)[kept_positions]))
    return (centroids[:, :3].astype(np.float32),
            centroids[:, 3].astype(np.float32), provenance)


def _trace(stage: str, input_points: int, output_points: int, latency_ms: float | None,
           reason_counts: dict, status: str = "completed", **extra) -> dict:
    return {
        "stage": stage, "status": status,
        "input_points": int(input_points), "output_points": int(output_points),
        "rejected_points": max(int(input_points) - int(output_points), 0),
        "latency_ms": None if latency_ms is None else round(float(latency_ms), 3),
        "reason_counts": reason_counts, **extra,
    }
