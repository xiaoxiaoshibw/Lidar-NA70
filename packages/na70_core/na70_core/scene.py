"""Pose-driven scene reconstruction for NA70 scans.

The builder owns only the assembly step: interpolate a trajectory, transform
each returned point with the pose at *its* time, voxel-downsample, and keep a
source reference for every map point.  It does not estimate poses, register
scans, or invent a clock.  Poses and their time base must come from a
traceable source (LIO run, turntable log, or a synthetic truth for tests).

Conventions:

* ``T_AB`` maps coordinates from frame B into frame A.
* ``Trajectory`` stores ``T_map_body`` as positions and ``wxyz`` quaternions.
* ``T_imu_lidar`` is required when the trajectory is in the IMU frame; the
  builder then composes ``T_map_lidar = T_map_imu @ T_imu_lidar``.
* A query outside the trajectory or across a gap longer than ``max_gap_s`` is
  dropped and counted -- never extrapolated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path

import numpy as np

from .lio import extrinsics


LIMITATIONS = (
    "地图质量取决于输入位姿；本模块不产生、不优化位姿",
    "包级时间仍是近似：逐点时间若由包时间复制，必须随结果声明",
    "跨轨迹缺口/超出覆盖的点被丢弃，不插值外推",
    "体素代表点是首个实测点，不是质心；来源引用保留",
    "当前实现把全段体素保存在内存；长录（数分钟以上）需分块/流式落盘，尚未实现",
)


def _as_array(values, name, shape):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != len(shape) or any(size not in (None, actual)
                                      for size, actual in zip(shape, array.shape)):
        raise ValueError(f"{name} must have shape {shape}")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
    return array


def quaternion_to_matrix(quaternions):
    """Convert ``wxyz`` quaternions ``(..., 4)`` to rotation matrices ``(..., 3, 3)``."""
    q = np.asarray(quaternions, dtype=np.float64)
    if q.shape[-1] != 4:
        raise ValueError("quaternions must have a trailing dimension of four")
    norms = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(norms <= 0) or not np.isfinite(norms).all():
        raise ValueError("quaternions must be finite and non-zero")
    w, x, y, z = (q / norms)[..., 0], (q / norms)[..., 1], (q / norms)[..., 2], (q / norms)[..., 3]
    matrix = np.empty(q.shape[:-1] + (3, 3), dtype=np.float64)
    matrix[..., 0, 0] = 1 - 2 * (y * y + z * z)
    matrix[..., 0, 1] = 2 * (x * y - w * z)
    matrix[..., 0, 2] = 2 * (x * z + w * y)
    matrix[..., 1, 0] = 2 * (x * y + w * z)
    matrix[..., 1, 1] = 1 - 2 * (x * x + z * z)
    matrix[..., 1, 2] = 2 * (y * z - w * x)
    matrix[..., 2, 0] = 2 * (x * z - w * y)
    matrix[..., 2, 1] = 2 * (y * z + w * x)
    matrix[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return matrix


def _slerp(q0, q1, fraction):
    dot = np.sum(q0 * q1, axis=-1)
    q1 = np.where(dot[..., None] < 0, -q1, q1)
    dot = np.clip(np.abs(dot), -1.0, 1.0)
    theta = np.arccos(dot)
    sine = np.sin(theta)
    small = sine < 1e-9
    s0 = np.where(small, 1.0 - fraction, np.sin((1.0 - fraction) * theta) / np.where(small, 1.0, sine))
    s1 = np.where(small, fraction, np.sin(fraction * theta) / np.where(small, 1.0, sine))
    result = s0[..., None] * q0 + s1[..., None] * q1
    norms = np.linalg.norm(result, axis=-1, keepdims=True)
    return result / np.where(norms > 0, norms, 1.0)


@dataclass(frozen=True)
class Trajectory:
    """Time-tagged ``T_map_body`` samples with a declared body frame."""

    times: np.ndarray
    positions: np.ndarray
    quaternions: np.ndarray
    frame: str = "imu"

    def __post_init__(self):
        times = _as_array(self.times, "times", (None,))
        positions = _as_array(self.positions, "positions", (None, 3))
        quaternions = _as_array(self.quaternions, "quaternions", (None, 4))
        if len(times) < 2:
            raise ValueError("trajectory needs at least two samples")
        if len(positions) != len(times) or len(quaternions) != len(times):
            raise ValueError("trajectory arrays must have equal length")
        if np.any(np.diff(times) <= 0):
            raise ValueError("trajectory times must strictly increase")
        if self.frame not in ("imu", "lidar"):
            raise ValueError("frame must be 'imu' or 'lidar'")
        norms = np.linalg.norm(quaternions, axis=1)
        if np.any(norms <= 0):
            raise ValueError("trajectory quaternions must be non-zero")
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "quaternions", quaternions / norms[:, None])

    def interpolate(self, query_times, *, max_gap_s=0.2):
        """Return ``(positions, rotations, valid, reasons)`` at query times.

        ``valid`` is false outside the covered span or inside an interval
        longer than ``max_gap_s``.  No extrapolation is performed.
        """
        query = np.asarray(query_times, dtype=np.float64)
        if query.ndim != 1:
            raise ValueError("query_times must be 1-D")
        if not math.isfinite(float(max_gap_s)) or max_gap_s <= 0:
            raise ValueError("max_gap_s must be finite and positive")
        if len(query) == 0:
            empty = np.empty((0, 3))
            return empty, np.empty((0, 3, 3)), np.zeros(0, dtype=bool), {
                "outside": 0, "gap": 0, "nonfinite": 0, "requested": 0}
        valid = np.isfinite(query)
        if not valid.all():
            finite_query = np.where(valid, query, self.times[0])
        else:
            finite_query = query
        right = np.searchsorted(self.times, finite_query, side="right")
        left = np.clip(right - 1, 0, len(self.times) - 1)
        right = np.clip(right, 0, len(self.times) - 1)
        span = self.times[right] - self.times[left]
        fraction = np.where(span > 0, (finite_query - self.times[left]) / np.where(span > 0, span, 1.0), 0.0)
        fraction = np.clip(fraction, 0.0, 1.0)
        outside = (finite_query < self.times[0]) | (finite_query > self.times[-1])
        # A query exactly on a sample needs no interpolation; only interior
        # points of an over-long interval are rejected.
        gap = (span > max_gap_s) & (fraction > 0) & ~outside
        valid &= ~outside & ~gap
        positions = self.positions[left] * (1.0 - fraction[:, None]) + self.positions[right] * fraction[:, None]
        quaternions = _slerp(self.quaternions[left], self.quaternions[right], fraction)
        rotations = quaternion_to_matrix(quaternions)
        invalid_queries = ~np.isfinite(query)
        reasons = {"outside": int(outside.sum()), "gap": int(gap.sum()),
                   "nonfinite": int(invalid_queries.sum()), "requested": int(len(query))}
        return positions, rotations, valid, reasons


def _rigid(matrix, name):
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError(f"{name} must be a finite 4x4 matrix")
    forward, inverse = extrinsics(matrix)
    return forward, inverse


def transform_points(points, point_times, trajectory, *, T_imu_lidar=None, max_gap_s=0.2):
    """Transform LiDAR points to the map frame using the pose at each point time.

    Returns ``(xyz_map, intensity, valid, reasons)``.  ``points`` may be
    ``Nx3`` or ``Nx4`` (the fourth column is intensity).  When the trajectory is
    in the IMU frame, ``T_imu_lidar`` must be supplied; an identity substitute
    would silently corrupt every map point.
    """
    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] not in (3, 4):
        raise ValueError("points must be Nx3 or Nx4")
    if len(values) != len(point_times):
        raise ValueError("points and point_times must have equal length")
    if not np.isfinite(values[:, :3]).all():
        raise ValueError("points contain non-finite values")
    if trajectory.frame == "imu":
        if T_imu_lidar is None:
            raise ValueError("T_imu_lidar is required when the trajectory frame is 'imu'")
        forward, _ = _rigid(T_imu_lidar, "T_imu_lidar")
        R_IL = forward[:3, :3]
        t_IL = forward[:3, 3]
    else:
        if T_imu_lidar is not None:
            raise ValueError("T_imu_lidar is only valid for an IMU-frame trajectory")
        R_IL = np.eye(3)
        t_IL = np.zeros(3)
    positions, rotations, valid, reasons = trajectory.interpolate(point_times, max_gap_s=max_gap_s)
    R_map_lidar = rotations @ R_IL
    t_map_lidar = rotations @ t_IL + positions
    transformed = np.einsum("nij,nj->ni", R_map_lidar, values[:, :3]) + t_map_lidar
    intensity = None
    if values.shape[1] == 4:
        intensity = np.clip(np.rint(values[:, 3]), 0, 255).astype(np.uint8)
    return transformed, intensity, valid, reasons


def voxel_downsample(points, voxel_size, *, counts=None):
    """Keep the first point per ``voxel_size`` cube.

    Returns ``(indices, inverse, voxel_counts)`` where ``indices`` index the
    representative rows of the input, ``inverse`` maps representatives to unique
    voxels, and ``voxel_counts`` sums the optional per-point weights.
    """
    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("points must be Nx3")
    if not math.isfinite(float(voxel_size)) or voxel_size <= 0:
        raise ValueError("voxel_size must be finite and positive")
    if len(values) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64)
    keys = np.floor(values / voxel_size).astype(np.int64)
    _, indices, inverse, multiplicity = np.unique(keys, axis=0, return_index=True,
                                                  return_inverse=True, return_counts=True)
    weights = np.ones(len(values), dtype=np.float64) if counts is None else np.asarray(counts, dtype=np.float64)
    if weights.shape != (len(values),):
        raise ValueError("counts must match the number of points")
    summed = np.bincount(inverse, weights=weights).astype(np.float64)
    return indices.astype(np.int64), inverse.astype(np.int64), summed


@dataclass
class MapResult:
    points: np.ndarray
    intensity: np.ndarray
    observations: np.ndarray
    source_frame: np.ndarray
    stats: dict

    def save_ply(self, path):
        path = Path(path)
        record = np.zeros(len(self.points), dtype=np.dtype([
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("intensity", "u1"),
            ("observations", "<u4"), ("source_frame", "<i4")]))
        record["x"], record["y"], record["z"] = self.points[:, 0], self.points[:, 1], self.points[:, 2]
        record["intensity"] = self.intensity
        record["observations"] = self.observations
        record["source_frame"] = self.source_frame
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(
                b"ply\nformat binary_little_endian 1.0\n"
                + f"element vertex {len(record)}\n".encode()
                + b"property float x\nproperty float y\nproperty float z\n"
                + b"property uchar intensity\nproperty uint observations\nproperty int source_frame\n"
                + b"end_header\n")
            handle.write(record.tobytes())
        return len(record)


@dataclass
class MapBuilder:
    """Incremental per-frame voxel accumulation with source provenance."""

    trajectory: Trajectory
    T_imu_lidar: object = None
    voxel_size: float = 0.05
    max_gap_s: float = 0.2
    _chunks: list = field(default_factory=list, init=False)
    _frames: list = field(default_factory=list, init=False)
    _dropped: dict = field(default_factory=lambda: {"outside": 0, "gap": 0, "nonfinite": 0}, init=False)

    def __post_init__(self):
        if not math.isfinite(float(self.voxel_size)) or self.voxel_size <= 0:
            raise ValueError("voxel_size must be finite and positive")
        if not math.isfinite(float(self.max_gap_s)) or self.max_gap_s <= 0:
            raise ValueError("max_gap_s must be finite and positive")
        if self.trajectory.frame == "imu" and self.T_imu_lidar is None:
            raise ValueError("T_imu_lidar is required for an IMU-frame trajectory")
        if self.trajectory.frame == "lidar" and self.T_imu_lidar is not None:
            raise ValueError("T_imu_lidar is only valid for an IMU-frame trajectory")
        if self.T_imu_lidar is not None:
            _rigid(self.T_imu_lidar, "T_imu_lidar")

    def add_frame(self, points, point_times, *, frame_index):
        if not isinstance(frame_index, int) or frame_index < 0:
            raise ValueError("frame_index must be a non-negative integer")
        values = np.asarray(points)
        transformed, intensity, valid, reasons = transform_points(
            values, point_times, self.trajectory, T_imu_lidar=self.T_imu_lidar,
            max_gap_s=self.max_gap_s)
        for key in self._dropped:
            self._dropped[key] += reasons.get(key, 0)
        report = {"frame_index": frame_index, "input_points": int(len(values)),
                  "valid_points": int(valid.sum()), "dropped": reasons}
        if not valid.any():
            self._frames.append(report)
            return report
        xyz = transformed[valid]
        indices, _, counts = voxel_downsample(xyz, self.voxel_size)
        xyz = xyz[indices]
        intensity = (np.zeros(len(values), dtype=np.uint8) if intensity is None else intensity)[valid][indices]
        source = np.nonzero(valid)[0][indices].astype(np.int64)
        self._chunks.append((xyz, intensity, counts, np.full(len(xyz), frame_index, dtype=np.int32), source))
        report["voxels"] = int(len(xyz))
        self._frames.append(report)
        return report

    def finish(self):
        if not self._chunks:
            empty = MapResult(np.empty((0, 3), dtype=np.float32), np.empty(0, dtype=np.uint8),
                              np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.int32),
                              {"points": 0, "voxels": 0, "frames": len(self._frames),
                               "dropped": dict(self._dropped), "limitations": list(LIMITATIONS)})
            return empty
        points = np.concatenate([chunk[0] for chunk in self._chunks], axis=0)
        intensity = np.concatenate([chunk[1] for chunk in self._chunks])
        counts = np.concatenate([chunk[2] for chunk in self._chunks])
        frames = np.concatenate([chunk[3] for chunk in self._chunks])
        source = np.concatenate([chunk[4] for chunk in self._chunks])
        indices, inverse, observations = voxel_downsample(points, self.voxel_size, counts=counts)
        result = MapResult(
            points=points[indices].astype(np.float32),
            intensity=intensity[indices],
            observations=observations.astype(np.uint32),
            source_frame=frames[indices],
            stats={
                "points": int(len(indices)),
                "voxels": int(len(indices)),
                "input_points": int(sum(frame["input_points"] for frame in self._frames)),
                "valid_points": int(sum(frame["valid_points"] for frame in self._frames)),
                "frames": len(self._frames),
                "frames_with_points": int(sum(1 for frame in self._frames if frame.get("voxels"))),
                "dropped": dict(self._dropped),
                "voxel_size_m": float(self.voxel_size),
                "bounds": {"minimum": points[indices].min(axis=0).tolist(),
                           "maximum": points[indices].max(axis=0).tolist()},
                "mean_points_per_voxel": float(observations.mean()),
                "max_points_per_voxel": int(observations.max()),
                "trajectory_frame": self.trajectory.frame,
                "extrinsic_supplied": self.trajectory.frame == "imu" and self.T_imu_lidar is not None,
                "limitations": list(LIMITATIONS),
            })
        return result