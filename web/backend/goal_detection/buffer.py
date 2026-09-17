"""Fixed-capacity frame ring buffer for sliding-window accumulation."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np


@dataclass
class StampedCloud:
    """One decoded frame: (N,4) float32 x,y,z,intensity plus replay metadata."""

    points: np.ndarray
    frame_index: int
    device_time: float | None = None
    complete: bool = True

    def __post_init__(self) -> None:
        points = np.ascontiguousarray(self.points, dtype="<f4")
        if points.ndim != 2 or points.shape[1] != 4:
            raise ValueError("点云必须是 (N, 4) 的 x,y,z,intensity 矩阵")
        self.points = points
        self.frame_index = int(self.frame_index)
        if self.device_time is not None:
            self.device_time = float(self.device_time)

    @property
    def xyz(self) -> np.ndarray:
        return self.points[:, :3]


@dataclass
class FrameBuffer:
    """FIFO window with a fixed capacity; the oldest frame is evicted first.

    Memory is bounded by ``capacity`` regardless of capture length.  Insertion
    order is the window order; a backwards ``device_time`` is tolerated but
    reported through ``monotonic`` so callers can flag replay glitches instead
    of silently reordering.
    """

    capacity: int
    frames: deque = field(default_factory=deque, init=False)
    monotonic: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if int(self.capacity) < 1:
            raise ValueError("capacity 至少为 1")
        self.capacity = int(self.capacity)

    def push(self, cloud: StampedCloud) -> None:
        if self.frames:
            previous = self.frames[-1]
            if previous.device_time is not None and cloud.device_time is not None \
                    and cloud.device_time < previous.device_time:
                self.monotonic = False
            if cloud.frame_index <= previous.frame_index:
                raise ValueError("帧索引必须单调递增")
        self.frames.append(cloud)
        while len(self.frames) > self.capacity:
            self.frames.popleft()

    def window(self) -> list[StampedCloud]:
        return list(self.frames)

    def clear(self) -> None:
        self.frames.clear()
        self.monotonic = True

    def is_full(self) -> bool:
        return len(self.frames) >= self.capacity

    def last(self) -> StampedCloud | None:
        return self.frames[-1] if self.frames else None

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)
