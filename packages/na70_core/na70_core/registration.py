"""Deterministic multi-station point-cloud registration (P2 station merge).

Pure numpy + scipy implementation, so it runs inside the locked web venv
(scipy is locked, Open3D is not) and stays reproducible:

- mean-preserving voxel averaging with per-voxel observation weights,
- weighted Kabsch (point-to-point SVD) with a reflection guard,
- coarse-to-fine trimmed point-to-point ICP (no random sampling anywhere),
- sequential station merging with explicit fitness gates.

Honesty boundaries: this module only aligns clouds to each other.  Fitness and
RMSE are self-consistency numbers of the merged clouds, not accuracy against
truth.  A merged output is expressed in the first station's own sensor frame,
which is arbitrary.  Stations need real geometric overlap; low-fitness inputs
must be refused, never averaged away.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LIMITATIONS = (
    "fitness/RMSE 只是两次点云间自洽性指标，不是对真值的精度",
    "合并结果位于第一个站位的传感器坐标系，该坐标系本身是任意的",
    "低 fitness 的配准会被显式拒绝，不会被静默平均掉",
    "ICP 假设两站几何主要是刚体差异；非刚性畸变（未标定外参、时间未同步）会污染结果",
    "ICP 可能收敛到局部极小；过强的残差裁剪在裁剪边缘会引入系统性偏置（SYNTHETIC 房间场景实测 trim>=0.25 会锁定错误解）",
    "yaw 多起点只覆盖偏航盆：真实教室静止单站实测（周期性桌椅结构）8/16/24 个 seed 全部落入"
    "自洽错误锁（fitness 0.998），真值盆需要平移先验才能进入；操作员方位先验与合并视图人工审计是必要防线",
)

MIN_POINTS = 16
MIN_PAIRS = 10
MIN_TRIM_PAIRS = 20


@dataclass(frozen=True)
class VoxelCloud:
    """Voxel-averaged cloud: one entry per occupied voxel."""

    centers: np.ndarray  # (N, 3) voxel centre coordinates
    means: np.ndarray  # (N, 3) weighted mean of observed points in the voxel
    weights: np.ndarray  # (N,) total observation weight in the voxel

    @property
    def size(self) -> int:
        return int(self.centers.shape[0])


def _as_xyz(xyz, name: str) -> np.ndarray:
    array = np.asarray(xyz, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError(f"{name} must be an (N, 3) array")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite points")
    if array.shape[0] == 0:
        raise ValueError(f"{name} is empty")
    return array


def _as_weights(weights, count: int, name: str) -> np.ndarray:
    if weights is None:
        return np.ones(count, dtype=np.float64)
    array = np.asarray(weights, dtype=np.float64).reshape(-1)
    if array.shape[0] != count:
        raise ValueError(f"{name} must have one weight per point")
    if not np.isfinite(array).all() or (array < 0).any() or array.sum() <= 0:
        raise ValueError(f"{name} must be finite, non-negative and not all zero")
    return array


def voxel_average(xyz, voxel: float, weights=None) -> VoxelCloud:
    """Mean-preserving voxelization.

    Each occupied voxel keeps the weighted mean of the points that fell in it
    and the summed weight (observation count when weights are counts), so a
    merged cloud never drifts toward voxel centres.
    """
    points = _as_xyz(xyz, "xyz")
    if not np.isfinite(voxel) or voxel <= 0:
        raise ValueError("voxel must be a positive finite size")
    weights = _as_weights(weights, points.shape[0], "weights")
    keys = np.floor(points / voxel).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    weight_totals = np.bincount(inverse, weights=weights)
    means = np.column_stack(
        [np.bincount(inverse, weights=weights * points[:, axis]) / weight_totals
         for axis in range(3)]
    )
    centers = (unique + 0.5) * voxel
    return VoxelCloud(centers=centers, means=means, weights=weight_totals)


def merge_voxel_clouds(a: VoxelCloud, b: VoxelCloud, voxel: float) -> VoxelCloud:
    """Re-voxelize two averaged clouds together, weighted by observation counts.

    Associative up to floating point: merging in any grouping yields the same
    weighted means, which is what lets stations accumulate incrementally.
    """
    points = np.concatenate([a.means, b.means], axis=0)
    weights = np.concatenate([a.weights, b.weights], axis=0)
    return voxel_average(points, voxel, weights=weights)


def transform_cloud(cloud: VoxelCloud, rotation: np.ndarray,
                    translation: np.ndarray) -> VoxelCloud:
    """Apply a rigid transform to a voxel cloud (means move, weights follow)."""
    rotation = np.asarray(rotation, dtype=np.float64)
    translation = np.asarray(translation, dtype=np.float64).reshape(3)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("rotation must be a finite 3x3 matrix")
    if not np.isfinite(translation).all():
        raise ValueError("translation must be finite")
    return VoxelCloud(centers=cloud.centers @ rotation.T + translation,
                      means=cloud.means @ rotation.T + translation,
                      weights=cloud.weights)


def kabsch(src, dst, weights=None):
    """Weighted point-to-point Kabsch fit: returns (R, t) with det(R) = +1."""
    src = _as_xyz(src, "src")
    dst = _as_xyz(dst, "dst")
    if src.shape != dst.shape:
        raise ValueError("src and dst must have the same shape")
    if src.shape[0] < 3:
        raise ValueError("at least 3 pairs are required")
    weights = _as_weights(weights, src.shape[0], "weights")
    src_mean = weights @ src / weights.sum()
    dst_mean = weights @ dst / weights.sum()
    src_centered = src - src_mean
    dst_centered = dst - dst_mean
    covariance = src_centered.T @ (weights[:, None] * dst_centered)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt = vt.copy()
        vt[-1] *= -1
        rotation = vt.T @ u.T
    translation = dst_mean - rotation @ src_mean
    return rotation, translation


def rotation_angle_deg(rotation: np.ndarray) -> float:
    """Rotation magnitude of a 3x3 matrix, in degrees (numeric-safe)."""
    trace = np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0)
    return float(np.degrees(np.arccos(trace)))


@dataclass(frozen=True)
class Registration:
    """Outcome of one ICP alignment of ``source`` onto ``target``."""

    rotation: np.ndarray  # (3, 3) map source -> target frame
    translation: np.ndarray  # (3,) map source -> target frame
    fitness: float  # inlier source voxels / source voxels, final level
    rmse: float  # inlier residual RMS, final level
    iterations: int  # total across all levels
    converged: bool  # final level reached the delta tolerances
    source_voxels: int
    target_voxels: int
    correspondences: int  # inlier pairs at the final level
    seeds: tuple = ()  # (yaw_deg, fitness, rmse, converged) per multistart seed

    def describe(self) -> dict:
        described = {
            "rotation": self.rotation.tolist(),
            "translation": self.translation.tolist(),
            "fitness": self.fitness,
            "rmse_m": self.rmse,
            "iterations": self.iterations,
            "converged": self.converged,
            "source_voxels": self.source_voxels,
            "target_voxels": self.target_voxels,
            "correspondences": self.correspondences,
        }
        if self.seeds:
            described["seeds"] = [
                {"yaw_deg": yaw, "fitness": fitness, "rmse_m": rmse,
                 "converged": converged}
                for yaw, fitness, rmse, converged in self.seeds]
        return described


def _as_initial(initial):
    """Validate an optional (rotation, translation) ICP seed."""
    if initial is None:
        return np.eye(3), np.zeros(3)
    rotation = np.asarray(initial[0], dtype=np.float64)
    translation = np.asarray(initial[1], dtype=np.float64).reshape(3)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("initial rotation must be a finite 3x3 matrix")
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or \
            not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-6):
        raise ValueError("initial rotation must be a proper rotation")
    if not np.isfinite(translation).all():
        raise ValueError("initial translation must be finite")
    return rotation, translation


def _correspondence_residuals(rotation, translation, source, target_tree):
    moved = source @ rotation.T + translation
    distance, index = target_tree.query(moved, workers=-1)
    return moved, distance, index


def icp_point_to_point(source, target, *, voxel_schedule=(1.0, 0.35, 0.12),
                       trim_fraction: float = 0.1,
                       correspondence_factor: float = 3.0,
                       max_iterations: int = 40,
                       rotation_tolerance_deg: float = 1e-3,
                       translation_tolerance_m: float = 1e-3,
                       initial=None) -> Registration:
    """Coarse-to-fine trimmed point-to-point ICP.

    Both clouds are voxel-averaged per level; correspondence weight comes from
    voxel observation counts.  Each iteration keeps pairs within
    ``correspondence_factor * voxel`` and additionally trims the worst
    ``trim_fraction`` of residuals, which tolerates partial overlap without
    any random sampling (fully deterministic).  ``initial`` optionally seeds
    the transform as an ``(rotation, translation)`` pair; ICP can still converge
    to a local minimum, so prefer ``icp_multistart`` when the relative yaw is
    unknown.
    """
    source = _as_xyz(source, "source")
    target = _as_xyz(target, "target")
    from scipy.spatial import cKDTree  # lazy, like map_quality/plane_extrinsic

    if source.shape[0] < MIN_POINTS or target.shape[0] < MIN_POINTS:
        raise ValueError(f"both clouds need at least {MIN_POINTS} points")
    if not voxel_schedule:
        raise ValueError("voxel_schedule must list at least one voxel size")
    schedule = [float(size) for size in voxel_schedule]
    if any(size <= 0 or not np.isfinite(size) for size in schedule):
        raise ValueError("voxel sizes must be positive and finite")
    if any(b >= a for a, b in zip(schedule, schedule[1:])):
        raise ValueError("voxel_schedule must be strictly decreasing")
    if not 0 <= trim_fraction < 1:
        raise ValueError("trim_fraction must be in [0, 1)")
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    rotation, translation = _as_initial(initial)
    iterations = 0
    converged = False
    source_cloud = target_cloud = None
    source_tree_points = None
    tree = None
    for voxel in schedule:
        source_cloud = voxel_average(source, voxel)
        target_cloud = voxel_average(target, voxel)
        tree = cKDTree(target_cloud.means)
        tau = correspondence_factor * voxel
        for _ in range(max_iterations):
            iterations += 1
            _, distance, index = _correspondence_residuals(
                rotation, translation, source_cloud.means, tree)
            inliers = distance <= tau
            if int(inliers.sum()) < MIN_PAIRS:
                converged = False
                break
            if trim_fraction > 0 and int(inliers.sum()) >= MIN_TRIM_PAIRS:
                cut = np.quantile(distance[inliers], 1.0 - trim_fraction)
                inliers &= distance <= max(cut, np.finfo(float).tiny)
            if int(inliers.sum()) < MIN_PAIRS:
                converged = False
                break
            new_rotation, new_translation = kabsch(
                source_cloud.means[inliers], target_cloud.means[index[inliers]],
                weights=source_cloud.weights[inliers])
            delta_rot = rotation_angle_deg(new_rotation @ rotation.T)
            delta_trans = float(np.linalg.norm(new_translation - translation))
            rotation, translation = new_rotation, new_translation
            if delta_rot <= rotation_tolerance_deg and delta_trans <= translation_tolerance_m:
                converged = True
                break

    final_voxel = schedule[-1]
    source_cloud = voxel_average(source, final_voxel)
    target_cloud = voxel_average(target, final_voxel)
    tree = cKDTree(target_cloud.means)
    tau = correspondence_factor * final_voxel
    _, distance, index = _correspondence_residuals(
        rotation, translation, source_cloud.means, tree)
    inliers = distance <= tau
    correspondences = int(inliers.sum())
    fitness = correspondences / max(source_cloud.size, 1)
    rmse = (float(np.sqrt(np.mean(distance[inliers] ** 2)))
            if correspondences else float("inf"))
    return Registration(
        rotation=rotation, translation=translation, fitness=fitness,
        rmse=rmse, iterations=iterations, converged=converged,
        source_voxels=int(source_cloud.size),
        target_voxels=int(target_cloud.size),
        correspondences=correspondences)


def _rotation_about_z(angle_deg: float) -> np.ndarray:
    angle = np.radians(angle_deg)
    cos, sin = np.cos(angle), np.sin(angle)
    return np.array([[cos, -sin, 0.0], [sin, cos, 0.0], [0.0, 0.0, 1.0]])


def icp_multistart(source, target, *, yaw_starts: int = 8,
                   initial=None, min_fitness: float = 0.0,
                   **icp_kwargs) -> Registration:
    """Deterministic yaw-grid multistart wrapper around ``icp_point_to_point``.

    A tripod-mounted station's dominant pose unknown is its yaw about the
    vertical axis.  The full ICP runs from a fixed grid of yaw seeds.  Among
    the seeds that clear ``min_fitness``, the one with the LOWEST RMSE wins:
    in partial-overlap scenes a wrong wall lock can reach a higher fitness
    than the true alignment (measured on SYNTHETIC rooms: fitness 0.84 vs 0.61
    at 4x worse RMSE and >10 m translation error), so fitness must only act as
    a floor, never as the ranking key.  If no seed clears the floor the
    best-fitness candidate is returned for the caller to gate.  No randomness;
    seed 0 is the initial guess itself.
    """
    source = _as_xyz(source, "source")
    target = _as_xyz(target, "target")
    if yaw_starts < 1:
        raise ValueError("yaw_starts must be >= 1")
    if not 0 <= min_fitness <= 1:
        raise ValueError("min_fitness must be in [0, 1]")
    base_rotation, base_translation = _as_initial(initial)
    candidates = []
    for seed in range(yaw_starts):
        yaw = 360.0 * seed / yaw_starts
        seed_rotation = _rotation_about_z(yaw) @ base_rotation
        seed_translation = _rotation_about_z(yaw) @ base_translation
        candidate = icp_point_to_point(source, target,
                                       initial=(seed_rotation, seed_translation),
                                       **icp_kwargs)
        candidates.append(candidate)
    gated = [c for c in candidates if c.fitness >= min_fitness]
    pool = gated if gated else candidates
    best = min(pool, key=lambda c: (c.rmse, -c.fitness))
    seeds = tuple((360.0 * index / yaw_starts, c.fitness, c.rmse, c.converged)
                  for index, c in enumerate(candidates))
    return Registration(
        rotation=best.rotation, translation=best.translation,
        fitness=best.fitness, rmse=best.rmse, iterations=best.iterations,
        converged=best.converged, source_voxels=best.source_voxels,
        target_voxels=best.target_voxels,
        correspondences=best.correspondences, seeds=seeds)


@dataclass(frozen=True)
class StationMerge:
    """Sequential merge of station clouds into the first station's frame."""

    merged: VoxelCloud
    registrations: dict  # label -> Registration (accepted ones)
    accepted: list
    refusals: list  # dicts: label, fitness, rmse, reason
    voxel: float
    frame_note: str = "merged cloud lives in the first station's sensor frame (arbitrary)"


def merge_stations(clouds, labels=None, *, voxel: float = 0.05,
                   fitness_gate: float = 0.30,
                   rmse_gate: float | None = None,
                   yaw_starts: int = 8,
                   initials=None,
                   **icp_kwargs) -> StationMerge:
    """Merge station clouds sequentially, refusing weak alignments.

    Station 0 defines the global frame.  Every later station is aligned to the
    merged-so-far cloud via ``icp_multistart`` (deterministic yaw grid); if
    fitness (or RMSE) fails the gate the station is excluded and reported,
    never averaged in.  ``initials`` optionally supplies one
    ``(rotation, translation)`` prior per non-anchor station, in station order.
    """
    if len(clouds) < 1:
        raise ValueError("merge_stations needs at least one cloud")
    if labels is None:
        labels = [f"station_{index:03d}" for index in range(len(clouds))]
    if len(labels) != len(clouds):
        raise ValueError("one label per cloud is required")
    if not 0 < fitness_gate <= 1:
        raise ValueError("fitness_gate must be in (0, 1]")
    if rmse_gate is not None and rmse_gate <= 0:
        raise ValueError("rmse_gate must be positive when given")

    prepared = []
    for index, cloud in enumerate(clouds):
        if isinstance(cloud, VoxelCloud):
            prepared.append(cloud)
        else:
            prepared.append(voxel_average(cloud, voxel))
    if prepared[0].size < MIN_POINTS:
        raise ValueError("first station cloud is too small to anchor a merge")

    merged = prepared[0]
    registrations: dict = {}
    refusals: list = []
    for index, (label, cloud) in enumerate(zip(labels[1:], prepared[1:])):
        initial = None
        if initials is not None:
            if len(initials) != len(clouds) - 1:
                raise ValueError("initials needs one (rotation, translation) "
                                 "per non-anchor station")
            initial = initials[index]
        registration = icp_multistart(cloud.means, merged.means,
                                      yaw_starts=yaw_starts, initial=initial,
                                      min_fitness=fitness_gate,
                                      **icp_kwargs)
        reasons = []
        if registration.fitness < fitness_gate:
            reasons.append(
                f"fitness {registration.fitness:.3f} < gate {fitness_gate:.3f}")
        if rmse_gate is not None and registration.rmse > rmse_gate:
            reasons.append(f"rmse {registration.rmse:.3f} m > gate {rmse_gate:.3f} m")
        if reasons:
            refusals.append({"label": label, "reason": "; ".join(reasons),
                             **registration.describe()})
            continue
        moved = transform_cloud(cloud, registration.rotation,
                                registration.translation)
        merged = merge_voxel_clouds(merged, moved, voxel)
        registrations[label] = registration
    return StationMerge(merged=merged, registrations=registrations,
                        accepted=[labels[0], *registrations.keys()],
                        refusals=refusals, voxel=float(voxel))
