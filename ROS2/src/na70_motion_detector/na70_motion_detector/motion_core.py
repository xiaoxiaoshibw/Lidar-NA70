"""ROS-independent voxel-background motion-presence detection core."""

from dataclasses import dataclass
from itertools import product
from typing import List

import numpy as np


@dataclass(frozen=True)
class MotionDetectorConfig:
    voxel_size: float = 0.15
    warmup_frames: int = 100
    background_min_hits: int = 3
    background_margin_voxels: int = 1
    min_range: float = 0.5
    max_range: float = 100.0
    min_z: float = -1000.0
    max_z: float = 1000.0
    min_moving_points: int = 30
    min_moving_ratio: float = 0.001
    confirmation_frames: int = 2
    clear_frames: int = 3

    def validate(self) -> None:
        if self.voxel_size <= 0.0:
            raise ValueError("voxel_size must be positive")
        if self.warmup_frames <= 0:
            raise ValueError("warmup_frames must be positive")
        if not 1 <= self.background_min_hits <= self.warmup_frames:
            raise ValueError("background_min_hits must be in [1, warmup_frames]")
        if self.background_margin_voxels < 0:
            raise ValueError("background_margin_voxels cannot be negative")
        if self.min_range < 0.0 or self.max_range <= self.min_range:
            raise ValueError("range limits are invalid")
        if self.max_z <= self.min_z:
            raise ValueError("z limits are invalid")
        if self.min_moving_points < 1:
            raise ValueError("min_moving_points must be positive")
        if not 0.0 <= self.min_moving_ratio <= 1.0:
            raise ValueError("min_moving_ratio must be in [0, 1]")
        if self.confirmation_frames < 1 or self.clear_frames < 1:
            raise ValueError("temporal thresholds must be positive")


@dataclass(frozen=True)
class MotionResult:
    ready: bool
    has_motion: bool
    moving_count: int
    considered_count: int
    moving_ratio: float
    moving_mask: np.ndarray
    warmup_frames_seen: int


def _row_view(values: np.ndarray) -> np.ndarray:
    contiguous = np.ascontiguousarray(values, dtype=np.int32)
    return contiguous.view(np.dtype((np.void, contiguous.dtype.itemsize * contiguous.shape[1]))).ravel()


class VoxelMotionDetector:
    """Learns a frozen empty-scene background and reports persistent changes."""

    def __init__(self, config: MotionDetectorConfig):
        config.validate()
        self.config = config
        self.reset()

    @property
    def ready(self) -> bool:
        return self._background_view is not None

    def reset(self) -> None:
        self._warmup_voxels: List[np.ndarray] = []
        self._warmup_frames_seen = 0
        self._background_view = None
        self._positive_streak = 0
        self._negative_streak = 0
        self._has_motion = False

    def _filter_points(self, points: np.ndarray) -> np.ndarray:
        xyz = points[:, :3]
        ranges = np.linalg.norm(xyz, axis=1)
        return (
            np.isfinite(points).all(axis=1)
            & (ranges >= self.config.min_range)
            & (ranges <= self.config.max_range)
            & (xyz[:, 2] >= self.config.min_z)
            & (xyz[:, 2] <= self.config.max_z)
        )

    def _voxelize(self, xyz: np.ndarray) -> np.ndarray:
        return np.floor(xyz / self.config.voxel_size).astype(np.int32)

    def _finalize_background(self) -> None:
        stacked = np.vstack(self._warmup_voxels)
        voxels, counts = np.unique(stacked, axis=0, return_counts=True)
        background = voxels[counts >= self.config.background_min_hits]

        margin = self.config.background_margin_voxels
        if margin > 0 and len(background):
            offsets = np.asarray(
                list(product(range(-margin, margin + 1), repeat=3)), dtype=np.int32
            )
            background = np.unique(
                (background[:, None, :] + offsets[None, :, :]).reshape(-1, 3), axis=0
            )

        self._background_view = np.unique(_row_view(background))
        self._warmup_voxels.clear()

    def process(self, points: np.ndarray) -> MotionResult:
        points = np.asarray(points)
        if points.ndim != 2 or points.shape[1] < 3:
            raise ValueError("points must have shape [N, >=3]")

        full_mask = np.zeros(len(points), dtype=bool)
        valid_mask = self._filter_points(points)
        filtered = points[valid_mask]
        considered_count = len(filtered)

        if not self.ready:
            self._warmup_frames_seen += 1
            if considered_count:
                frame_voxels = np.unique(self._voxelize(filtered[:, :3]), axis=0)
                self._warmup_voxels.append(frame_voxels)
            else:
                self._warmup_voxels.append(np.empty((0, 3), dtype=np.int32))

            if len(self._warmup_voxels) >= self.config.warmup_frames:
                self._finalize_background()

            return MotionResult(
                ready=self.ready,
                has_motion=False,
                moving_count=0,
                considered_count=considered_count,
                moving_ratio=0.0,
                moving_mask=full_mask,
                warmup_frames_seen=min(
                    self._warmup_frames_seen, self.config.warmup_frames
                ),
            )

        if considered_count:
            current_voxels = self._voxelize(filtered[:, :3])
            candidate_mask = ~np.isin(_row_view(current_voxels), self._background_view)
            full_mask[np.flatnonzero(valid_mask)] = candidate_mask

        moving_count = int(full_mask.sum())
        moving_ratio = moving_count / considered_count if considered_count else 0.0
        raw_motion = (
            moving_count >= self.config.min_moving_points
            and moving_ratio >= self.config.min_moving_ratio
        )

        if raw_motion:
            self._positive_streak += 1
            self._negative_streak = 0
            if self._positive_streak >= self.config.confirmation_frames:
                self._has_motion = True
        else:
            self._negative_streak += 1
            self._positive_streak = 0
            if self._negative_streak >= self.config.clear_frames:
                self._has_motion = False

        return MotionResult(
            ready=True,
            has_motion=self._has_motion,
            moving_count=moving_count,
            considered_count=considered_count,
            moving_ratio=moving_ratio,
            moving_mask=full_mask,
            warmup_frames_seen=self._warmup_frames_seen,
        )
