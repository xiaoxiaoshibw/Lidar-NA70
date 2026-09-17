"""LiDAR-only point-cloud odometry and voxel-map fusion.

Scope: static environment, slowly moved sensor, no IMU, no loop closure.
The pipeline is deliberately free of FastAPI and ROS2 imports so the whole chain
can be exercised offline against synthetic clouds.  ``mapping_jobs`` owns
persistence and the HTTP surface; every numerical decision lives here.

Pipeline (see ``Plan/MASTER_开发与验收总计划.md``)::

    NA70 frame
      -> range / NaN / Inf / zero filtering
      -> optional ROI
      -> voxel downsample
      -> statistical outlier removal
      -> constant-velocity initial guess (adjacent-frame relative pose)
      -> scan-to-local-submap ICP (point-to-point or point-to-plane)
      -> geometric degeneracy + quality gate
      -> pose, keyframe decision, segment bookkeeping
      -> local submap rebuild, voxel-map fusion

This is *point cloud odometry*, not SLAM: there is no descriptor, no loop
candidate search, no pose graph and no global optimisation.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

ODOMETRY_VERSION = "na70-lidar-odometry-v2"
MAP_VERSION = "na70-voxel-map-v1"
STAGE = "point_cloud_odometry"

#: Stable frame states.  ``quality_failed`` covers every rejection that still
#: produced a pose candidate; ``tracking_lost`` means the frame was not located
#: at all and therefore must never be fused into the map.
FRAME_STATES = (
    "reference", "accepted", "quality_failed", "insufficient_points",
    "degenerate_geometry", "timestamp_invalid", "tracking_lost", "reinitialized",
)
REJECTED_STATES = (
    "quality_failed", "insufficient_points", "degenerate_geometry", "timestamp_invalid",
)

REQUIRED_CONFIG = (
    "voxel_size", "max_points", "max_range", "min_points", "correspondence_distance",
    "max_iterations", "min_overlap", "max_rmse", "max_translation_step", "max_rotation_step",
    "keyframe_translation", "keyframe_rotation", "submap_keyframes", "map_voxel_size",
)


# --------------------------------------------------------------------------- config

def configuration(path=None) -> dict:
    """Load the reviewable threshold file.  JSON is a YAML subset."""
    source = Path(path) if path else Path(__file__).with_name("mapping.yaml")
    raw = json.loads(source.read_text("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("mapping 配置必须是对象")
    missing = [key for key in REQUIRED_CONFIG if key not in raw]
    if missing:
        raise ValueError("mapping 配置缺少字段：" + ", ".join(missing))
    if raw["voxel_size"] <= 0 or raw["map_voxel_size"] <= 0:
        raise ValueError("voxel_size 与 map_voxel_size 必须为正")
    if raw["min_points"] < 3:
        raise ValueError("min_points 至少为 3")
    return raw


def configuration_version(cfg: dict) -> str:
    """Stable hash so a stored result can be tied to the exact thresholds used."""
    body = json.dumps({key: cfg[key] for key in sorted(cfg)}, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def validate_configuration(cfg):
    defaults = configuration()
    unknown = set(cfg) - set(defaults)
    if unknown:
        raise ValueError("未知配置字段: " + ", ".join(sorted(unknown)))
    for key, default in defaults.items():
        value = cfg.get(key, default)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
        elif isinstance(default, str) and not isinstance(value, str):
            raise ValueError(f"{key} 必须为字符串")
        elif isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                raise ValueError(f"{key} 必须为有限数值")
            if isinstance(default, int) and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{key} 必须为非负整数")
            if value < 0:
                raise ValueError(f"{key} 不得为负")
    for key in ("voxel_size", "map_voxel_size", "submap_voxel_size", "correspondence_distance",
                "max_iterations", "chunk_voxels", "submap_frames", "lost_after_failures",
                "sweep_min_packets", "sweep_max_packets", "sweep_max_duration",
                "sweep_pitch_bin_size", "sweep_yaw_bin_size", "sweep_target_angular_bins"):
        if cfg[key] <= 0:
            raise ValueError(f"{key} 必须为正")
    if cfg["sweep_max_packets"] < cfg["sweep_min_packets"]:
        raise ValueError("sweep_max_packets 不得小于 sweep_min_packets")
    for key in ("min_overlap", "correspondence_trim", "min_geometry_ratio", "degeneracy_ratio"):
        if not 0 < cfg[key] <= 1:
            raise ValueError(f"{key} 必须在 (0, 1] 内")
    for key in ("sweep_min_angular_coverage", "se2_observability_ratio",
                "se2_structure_normal_z_max"):
        if not 0 < cfg[key] <= 1:
            raise ValueError(f"{key} 必须在 (0, 1] 内")
    if cfg["min_points"] < 3 or cfg["normal_neighbors"] < 3 or cfg["outlier_neighbors"] < 2:
        raise ValueError("点数或邻域不足")
    if cfg["max_range"] <= cfg["min_range"]:
        raise ValueError("max_range 必须大于 min_range")
    if cfg["method"] not in ("point_to_point", "point_to_plane"):
        raise ValueError("未知配准方法")
    if cfg["motion_model"] not in ("se2", "se3"):
        raise ValueError("未知运动模型")
    for key in ("gicp_resolutions", "ndt_resolutions"):
        resolutions = cfg[key]
        if (not isinstance(resolutions, list) or not resolutions
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not np.isfinite(value) or value <= 0 for value in resolutions)):
            raise ValueError(f"{key} 必须是正有限分辨率列表")
    if cfg["initialization_mode"] not in ("single_frame", "stationary_window"):
        raise ValueError("未知初始化模式")
    if cfg["initialization_mode"] == "stationary_window":
        start, end = cfg["stationary_start_frame"], cfg["stationary_end_frame"]
        if not isinstance(cfg["stationary_declaration"], str) or not cfg["stationary_declaration"].strip():
            raise ValueError("静止初始化需要明确的静止区间声明")
        if start > end or end - start + 1 > cfg["bootstrap_max_frames"]:
            raise ValueError("静止区间顺序错误或超过初始化帧数预算")
    distances = cfg["diagnostic_distances"]
    if not isinstance(distances, list) or not distances or any(
            isinstance(v, bool) or not isinstance(v, (float, int)) or not np.isfinite(v) or v <= 0 for v in distances):
        raise ValueError("diagnostic_distances 必须是正有限距离列表")
    roi = cfg.get("roi")
    if roi is not None:
        try:
            low, high = np.array(roi["min"], dtype=float), np.array(roi["max"], dtype=float)
            valid = low.shape == high.shape == (3,) and np.isfinite([low, high]).all() and (low < high).all()
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("ROI 必须包含有限且递增的三维 min/max")
    return cfg


# --------------------------------------------------------------------------- SE(3)

def transform(points: np.ndarray, pose: np.ndarray) -> np.ndarray:
    return np.asarray(points, dtype=np.float64) @ pose[:3, :3].T + pose[:3, 3]


def invert(pose: np.ndarray) -> np.ndarray:
    rotation = pose[:3, :3]
    out = np.eye(4)
    out[:3, :3] = rotation.T
    out[:3, 3] = -rotation.T @ pose[:3, 3]
    return out


def rotation_deg(pose: np.ndarray) -> float:
    cosine = (np.trace(pose[:3, :3]) - 1.0) / 2.0
    return float(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))


def translation_m(pose: np.ndarray) -> float:
    return float(np.linalg.norm(pose[:3, 3]))


def predict_pose(pose, increment, interval, elapsed, cfg):
    """Bounded constant body-twist extrapolation in SE(3)."""
    if interval is None or interval <= 0:
        return pose.copy(), "insufficient_history"
    if elapsed <= 0 or elapsed > cfg.get("max_prediction_seconds", 2.0):
        return pose.copy(), "prediction_horizon_exceeded"
    omega = Rotation.from_matrix(increment[:3, :3]).as_rotvec()
    def jacobian(w):
        theta = np.linalg.norm(w)
        x, y, z = w
        skew = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
        if theta < 1e-6:
            return np.eye(3) + .5 * skew + skew @ skew / 6
        return (np.eye(3) + (1 - np.cos(theta)) / theta**2 * skew
                + (theta - np.sin(theta)) / theta**3 * skew @ skew)
    ratio = elapsed / interval
    displacement = np.linalg.solve(jacobian(omega), increment[:3, 3])
    delta = np.eye(4)
    delta[:3, :3] = Rotation.from_rotvec(omega * ratio).as_matrix()
    delta[:3, 3] = jacobian(omega * ratio) @ (displacement * ratio)
    return pose @ delta, "constant_body_twist"


def _exp_so3(omega: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(omega))
    if angle < 1e-12:
        return np.eye(3)
    axis = omega / angle
    skew = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    return np.eye(3) + math.sin(angle) * skew + (1 - math.cos(angle)) * (skew @ skew)


# ------------------------------------------------------------------ preprocessing

def clean(points, cfg: dict):
    """Drop non-finite, zero-range and out-of-range returns.

    NaN and Inf coordinates are replaced by zero and then removed by the range
    filter, so a single pass rejects NaN/Inf/zero without a second allocation.
    """
    array = np.asarray(points)
    if array.ndim != 2 or array.shape[0] == 0 or array.shape[1] < 3:
        return np.empty((0, 3)), np.empty((0,))
    xyz = np.ascontiguousarray(array[:, :3], dtype=np.float64)
    intensity = np.ascontiguousarray(array[:, 3], dtype=np.float64) if array.shape[1] > 3 else np.zeros(len(array))
    finite = np.isfinite(xyz).all(axis=1)
    xyz = np.where(finite[:, None], xyz, 0.0)
    distance = np.linalg.norm(xyz, axis=1)
    keep = finite & np.isfinite(intensity) & (distance > 0) & (distance >= cfg.get("min_range", 0.0)) & (distance <= cfg["max_range"])
    roi = cfg.get("roi")
    if roi:
        keep &= np.all((xyz >= np.asarray(roi["min"], dtype=np.float64))
                       & (xyz <= np.asarray(roi["max"], dtype=np.float64)), axis=1)
    return xyz[keep], intensity[keep]


def downsample(xyz, intensity, voxel: float, limit: int = 0):
    """Voxel-centroid downsample; the centroid carries the mean intensity."""
    if len(xyz) == 0:
        return np.empty((0, 3)), np.empty((0,))
    keys = np.floor(np.asarray(xyz, dtype=np.float64) / voxel).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    counts = np.bincount(inverse, minlength=len(unique)).astype(np.float64)
    stacked = np.column_stack((np.asarray(xyz, dtype=np.float64), np.asarray(intensity, dtype=np.float64)))
    sums = np.zeros((len(unique), 4), dtype=np.float64)
    for column in range(4):
        sums[:, column] = np.bincount(inverse, weights=stacked[:, column], minlength=len(unique))
    centroids = sums / counts[:, None]
    if limit and len(centroids) > limit:
        centroids = centroids[np.linspace(0, len(centroids) - 1, int(limit), dtype=np.int64)]
    return centroids[:, :3], centroids[:, 3]


def remove_outliers(xyz, cfg: dict):
    neighbors = int(cfg.get("outlier_neighbors", 6))
    if len(xyz) <= neighbors:
        return np.ones(len(xyz), dtype=bool)
    distances, _ = cKDTree(xyz).query(xyz, k=neighbors)
    spread = distances[:, 1:].mean(axis=1)
    std = float(spread.std())
    threshold = float(spread.mean()) + float(cfg.get("outlier_std_ratio", 1.0)) * (std if std > 0 else 1e-9)
    return spread <= threshold


def preprocess(points, cfg: dict, stats=None):
    """Full front end: filter -> ROI -> voxel downsample -> outlier removal."""
    xyz, intensity = clean(points, cfg)
    if stats is not None:
        stats.update(raw_points=len(points), range_roi_points=len(xyz))
        raw = np.asarray(points)
        stats["finite_points"] = int(np.isfinite(raw[:, :min(4, raw.shape[1])]).all(axis=1).sum()) if raw.ndim == 2 else 0
        stats["range_points"] = len(clean(points, {**cfg, "roi": None})[0])
    if len(xyz) == 0:
        return xyz, intensity
    xyz, intensity = downsample(xyz, intensity, cfg["voxel_size"], cfg["max_points"])
    if stats is not None:
        stats["downsampled_points"] = len(xyz)
    if len(xyz) == 0:
        return xyz, intensity
    mask = remove_outliers(xyz, cfg)
    if stats is not None:
        stats["filtered_points"] = int(mask.sum())
    return xyz[mask], intensity[mask]


def spacing_statistics(source, submap, cfg):
    """Deterministic bounded query sampling; reference tree retains all points."""
    if len(source) < 2:
        return dict(sample_count=0, within_frame=None, to_submap=None)
    rng = np.random.default_rng(cfg.get("diagnostic_seed", 42))
    count = min(len(source), cfg.get("diagnostic_sample_points", 1000))
    sample = source[rng.choice(len(source), count, replace=False)]
    distances = cKDTree(source).query(sample, k=2)[0][:, 1]
    quantiles = lambda values: dict(zip(("p10", "p50", "p90"), np.quantile(values, [.1, .5, .9]).tolist()))
    result = dict(sample_count=count, seed=cfg.get("diagnostic_seed", 42),
                  stage="post_preprocess", within_frame=quantiles(distances), to_submap=None)
    if submap is not None:
        distances = submap["tree"].query(sample)[0]
        result["to_submap"] = quantiles(distances)
        result["coverage"] = {str(distance): float(np.mean(distances <= distance))
                              for distance in cfg.get("diagnostic_distances", [.3, .6, 1.0])}
    return result


def estimate_normals(xyz, neighbors: int = 10) -> np.ndarray:
    """PCA normals from a k-neighbourhood; used by point-to-plane ICP."""
    count = len(xyz)
    if count < 3:
        return np.zeros((count, 3))
    k = max(3, min(int(neighbors), count))
    _, indices = cKDTree(xyz).query(xyz, k=k)
    neighbours = np.asarray(xyz)[indices]
    centred = neighbours - neighbours.mean(axis=1, keepdims=True)
    covariance = np.einsum("nki,nkj->nij", centred, centred) / k
    _, vectors = np.linalg.eigh(covariance)
    return vectors[:, :, 0]


def is_planar(points, cfg: dict) -> bool:
    """True when the cloud is (near) rank-2, i.e. a single flat surface.

    The test compares the smallest principal extent with the *second* smallest,
    not the largest.  A room scan is a thin shell whose smallest extent is small
    simply because rooms are flat-ish; comparing against the largest extent would
    flag every real room as degenerate.  A genuine single plane collapses the
    smallest extent to zero while the in-plane extents stay large.
    """
    if len(points) < 3:
        return True
    spectrum = np.linalg.eigvalsh(np.cov(np.asarray(points, dtype=np.float64).T))
    if spectrum[1] <= 0:
        return True
    return bool(spectrum[0] < cfg.get("min_geometry_ratio", 0.02) * spectrum[1])


# ------------------------------------------------------------------- registration

def _point_to_plane_delta(moved, target, normals, cfg: dict, diagnostics=None):
    """One linearised point-to-plane step plus a conditioning verdict.

    The rotation columns of the Jacobian carry a length, so the system is scaled
    by the scene extent before solving.  Without that the six eigenvalues span
    several orders of magnitude purely because of units, and any degeneracy ratio
    would be meaningless.
    """
    residual = np.einsum("ij,ij->i", moved - target, normals)
    extent = float(np.median(np.linalg.norm(moved - moved.mean(axis=0), axis=1)))
    extent = extent if extent > 1e-3 else 1e-3
    jacobian = np.hstack((np.cross(moved, normals) / extent, normals))
    normal_matrix = jacobian.T @ jacobian
    spectrum = np.linalg.eigvalsh(normal_matrix)
    largest = float(spectrum[-1])
    if diagnostics is not None:
        diagnostics["normal_matrix_ratio"] = float(spectrum[0]) / largest if largest > 0 else None
    if largest <= 0:
        return np.eye(4), True
    degenerate = bool(float(spectrum[0]) / largest < cfg.get("degeneracy_ratio", 0.005))
    try:
        solution = np.linalg.lstsq(normal_matrix, -jacobian.T @ residual, rcond=1e-8)[0]
    except np.linalg.LinAlgError:
        return np.eye(4), True
    omega = solution[:3] / extent
    shift = solution[3:]
    angle = float(np.linalg.norm(omega))
    limit_angle = math.radians(float(cfg.get("max_iteration_rotation_deg", 5.0)))
    if angle > limit_angle > 0:
        omega = omega * (limit_angle / angle)
    limit_shift = float(cfg.get("max_iteration_translation", 0.5))
    distance = float(np.linalg.norm(shift))
    if distance > limit_shift > 0:
        shift = shift * (limit_shift / distance)
    delta = np.eye(4)
    delta[:3, :3] = _exp_so3(omega)
    delta[:3, 3] = shift
    return delta, degenerate


def _point_to_point_delta(moved, target, cfg: dict):
    """Closed-form rigid step via SVD of the centred cross-covariance."""
    source_centre = moved.mean(axis=0)
    target_centre = target.mean(axis=0)
    u, _, vt = np.linalg.svd((moved - source_centre).T @ (target - target_centre))
    correction = np.eye(3)
    correction[2, 2] = np.linalg.det(vt.T @ u.T)
    rotation = vt.T @ correction @ u.T
    delta = np.eye(4)
    delta[:3, :3] = rotation
    delta[:3, 3] = target_centre - rotation @ source_centre
    return delta, False


def register(source, target, initial, cfg: dict, tree=None, normals=None, reference=None):
    """Align ``source`` onto ``target`` starting from ``initial``.

    ``reference`` is the pose the increment limits are measured against.  It
    defaults to ``initial``, but the odometry loop passes the previous accepted
    pose: the initial guess may be arbitrarily bad (constant-velocity prior on a
    reversal), and clamping the *correction* would reject a frame that landed
    correctly.  What must be bounded is the motion between consecutive poses.

    Returns ``(pose | None, quality)``.  ``pose`` is only returned when every
    quality gate passes, so a caller can never fuse a frame that failed a gate.
    """
    method = cfg.get("method", "point_to_point")
    quality = dict(state=None, reason=None, overlap=0.0, rmse=None, translation_step_m=0.0,
                   rotation_step_m_deg=0.0, iterations=0, method=method, point_count=int(len(source)))
    if len(source) < cfg["min_points"] or len(target) < cfg["min_points"]:
        quality.update(state="insufficient_points", reason="too_few_points")
        return None, quality
    if is_planar(source, cfg):
        quality.update(state="degenerate_geometry", reason="planar_source")
        return None, quality

    tree = tree if tree is not None else cKDTree(target)
    source64 = np.asarray(source, dtype=np.float64)
    target64 = np.asarray(target, dtype=np.float64)
    pose = np.array(initial, dtype=np.float64)
    if method == "point_to_plane" and normals is None:
        normals = estimate_normals(target64, cfg.get("normal_neighbors", 10))

    iterations = 0
    for iterations in range(1, int(cfg["max_iterations"]) + 1):
        moved = source64 @ pose[:3, :3].T + pose[:3, 3]
        distances, indices = tree.query(moved, k=1)
        valid = distances < cfg["correspondence_distance"]
        if int(valid.sum()) < cfg["min_points"]:
            quality.update(state="insufficient_points", reason="no_correspondence", iterations=iterations)
            return None, quality
        cutoff = float(np.quantile(distances[valid], min(1.0, cfg.get("correspondence_trim", 0.85))))
        valid &= distances <= cutoff
        if int(valid.sum()) < cfg["min_points"]:
            quality.update(state="insufficient_points", reason="no_correspondence", iterations=iterations)
            return None, quality
        if method == "point_to_plane":
            delta, degenerate = _point_to_plane_delta(moved[valid], target64[indices[valid]], normals[indices[valid]], cfg, quality)
        else:
            delta, degenerate = _point_to_point_delta(moved[valid], target64[indices[valid]], cfg)
        if degenerate:
            quality.update(state="degenerate_geometry", reason="unobservable_direction", iterations=iterations)
            return None, quality
        pose = delta @ pose
        if float(np.linalg.norm(delta - np.eye(4))) < cfg.get("convergence_m", 0.0001):
            break

    moved = source64 @ pose[:3, :3].T + pose[:3, 3]
    distances, _ = tree.query(moved, k=1)
    valid = distances < cfg["correspondence_distance"]
    overlap = float(valid.mean()) if len(distances) else 0.0
    rmse = float(np.sqrt(np.mean(distances[valid] ** 2))) if bool(valid.any()) else None
    delta = invert(np.asarray(reference if reference is not None else initial, dtype=np.float64)) @ pose
    step_translation = translation_m(delta)
    step_rotation = rotation_deg(delta)
    quality.update(overlap=round(overlap, 4), rmse=rmse, translation_step_m=round(step_translation, 6),
                   rotation_step_m_deg=round(step_rotation, 6), iterations=iterations,
                   point_count=int(len(source)))
    # The un-gated result, kept only so a rejected frame can be *displayed* where
    # the solver put it.  It must never be fused: only the gated return value is.
    quality["candidate_pose"] = pose.tolist()

    if overlap < cfg["min_overlap"]:
        quality.update(state="quality_failed", reason="overlap_low")
    elif rmse is None or rmse > cfg["max_rmse"]:
        quality.update(state="quality_failed", reason="rmse_high")
    elif step_translation > cfg["max_translation_step"]:
        quality.update(state="quality_failed", reason="translation_jump")
    elif step_rotation > cfg["max_rotation_step"]:
        quality.update(state="quality_failed", reason="rotation_jump")
    else:
        quality.update(state="accepted", reason="accepted")
        return pose, quality
    return None, quality


# ---------------------------------------------------------------------- voxel map

class VoxelMap:
    """Voxel-hash accumulator.

    Every cell keeps the summed position and intensity, the observation count,
    the most recent observation time and the keyframe / trajectory segment that
    last contributed.  Cells are grouped into fixed index blocks so the WebUI can
    request only the chunks inside the current view.
    """

    def __init__(self, voxel: float, max_voxels: int = 0, chunk_voxels: int = 32):
        self.voxel = float(voxel)
        self.max_voxels = int(max_voxels)
        self.chunk_voxels = max(1, int(chunk_voxels))
        self.cells: dict = {}
        self.fused_points = 0
        self.dropped_points = 0
        self.minimum = None
        self.maximum = None

    def add(self, xyz, intensity, timestamp, keyframe_id: int, segment_id: int) -> None:
        if len(xyz) == 0:
            return
        xyz = np.asarray(xyz, dtype=np.float64)
        self.fused_points += int(len(xyz))
        low, high = xyz.min(axis=0), xyz.max(axis=0)
        self.minimum = low if self.minimum is None else np.minimum(self.minimum, low)
        self.maximum = high if self.maximum is None else np.maximum(self.maximum, high)

        keys = np.floor(xyz / self.voxel).astype(np.int64)
        values = np.column_stack((xyz, np.asarray(intensity, dtype=np.float64)))
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        inverse = inverse.reshape(-1)
        counts = np.bincount(inverse, minlength=len(unique)).astype(np.float64)
        sums = np.zeros((len(unique), 4), dtype=np.float64)
        for column in range(4):
            sums[:, column] = np.bincount(inverse, weights=values[:, column], minlength=len(unique))

        stamp = float(timestamp) if timestamp is not None and np.isfinite(timestamp) else 0.0
        for index, key in enumerate(unique):
            cell = self.cells.get((int(key[0]), int(key[1]), int(key[2])))
            observation = sums[index]
            seen = counts[index]
            if cell is None:
                if self.max_voxels and len(self.cells) >= self.max_voxels:
                    self.dropped_points += int(seen)
                    continue
                self.cells[(int(key[0]), int(key[1]), int(key[2]))] = [
                    observation[0], observation[1], observation[2], observation[3],
                    seen, stamp, int(keyframe_id), int(segment_id), 1,
                ]
            else:
                cell[0] += observation[0]
                cell[1] += observation[1]
                cell[2] += observation[2]
                cell[3] += observation[3]
                cell[4] += seen
                cell[5] = stamp
                cell[6] = int(keyframe_id)
                cell[7] = int(segment_id)
                cell[8] += 1

    def voxel_count(self) -> int:
        return len(self.cells)

    def bounds(self):
        if not self.cells:
            return None
        xyz = self._matrix()[:, :3]
        return dict(minimum=xyz.min(axis=0).tolist(), maximum=xyz.max(axis=0).tolist())

    def _row(self, key, cell):
        count = cell[4] if cell[4] > 0 else 1.0
        return [round(cell[0] / count, 4), round(cell[1] / count, 4), round(cell[2] / count, 4),
                round(cell[3] / count, 3), int(cell[4]), round(cell[5], 4), int(cell[6]), int(cell[7])]

    def _matrix(self) -> np.ndarray:
        """Every fused voxel as a row of centroids, for vectorised export."""
        matrix = np.zeros((len(self.cells), 8), dtype=np.float64)
        for index, cell in enumerate(self.cells.values()):
            seen = cell[4] if cell[4] > 0 else 1.0
            matrix[index] = (cell[0] / seen, cell[1] / seen, cell[2] / seen, cell[3] / seen,
                             cell[4], cell[5], cell[6], cell[7])
        return matrix

    def chunk_key(self, key):
        return tuple(int(v // self.chunk_voxels) for v in key)

    def chunked(self) -> dict:
        """``{chunk_id: [voxel rows]}`` without materialising repeated copies."""
        out: dict = {}
        for key, cell in self.cells.items():
            out.setdefault(self.chunk_key(key), []).append(self._row(key, cell))
        return out

    def segment_summary(self) -> list:
        summary: dict = {}
        for cell in self.cells.values():
            entry = summary.setdefault(int(cell[7]), dict(voxels=0, observations=0.0))
            entry["voxels"] += 1
            entry["observations"] += float(cell[4])
        return [dict(segment_id=key, **value) for key, value in sorted(summary.items())]

    def export_ply(self, path: Path) -> int:
        """Binary little-endian PLY: xyz float32, intensity uchar, observations uint32."""
        matrix = self._matrix()
        record = np.zeros(len(matrix), dtype=np.dtype([
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("intensity", "u1"), ("observations", "<u4"), ("keyframe_id", "<i4")]))
        record["x"] = matrix[:, 0]
        record["y"] = matrix[:, 1]
        record["z"] = matrix[:, 2]
        record["intensity"] = np.clip(np.rint(matrix[:, 3]), 0, 255).astype("u1")
        record["observations"] = np.maximum(matrix[:, 4], 0).astype("<u4")
        record["keyframe_id"] = matrix[:, 6].astype("<i4")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(
                b"ply\nformat binary_little_endian 1.0\n"
                + f"element vertex {len(record)}\n".encode()
                + b"property float x\nproperty float y\nproperty float z\n"
                + b"property uchar intensity\nproperty uint observations\nproperty int keyframe_id\n"
                + b"end_header\n"
            )
            handle.write(record.tobytes())
        return len(record)

    def export_pcd(self, path: Path) -> int:
        """Binary PCD with one fused centroid per voxel."""
        matrix = self._matrix()
        record = np.zeros(len(matrix), dtype=np.dtype([
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("intensity", "<f4"),
            ("observations", "<u4"), ("keyframe_id", "<i4"), ("segment_id", "<i4")]))
        record["x"] = matrix[:, 0]
        record["y"] = matrix[:, 1]
        record["z"] = matrix[:, 2]
        record["intensity"] = matrix[:, 3]
        record["observations"] = np.maximum(matrix[:, 4], 0).astype("<u4")
        record["keyframe_id"] = matrix[:, 6].astype("<i4")
        record["segment_id"] = matrix[:, 7].astype("<i4")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(
                b"# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\n"
                + b"FIELDS x y z intensity observations keyframe_id segment_id\n"
                + b"SIZE 4 4 4 4 4 4 4\nTYPE F F F F U I I\nCOUNT 1 1 1 1 1 1 1\n"
                + f"WIDTH {len(record)}\nHEIGHT 1\nPOINTS {len(record)}\nDATA binary\n".encode()
            )
            handle.write(record.tobytes())
        return len(record)

    def metadata(self, **extra) -> dict:
        chunks = self.chunked()
        bounds = self.bounds() or dict(minimum=None, maximum=None)
        payload = dict(
            version=MAP_VERSION,
            voxel_size=self.voxel,
            chunk_voxels=self.chunk_voxels,
            fused_points=self.fused_points,
            dropped_points=self.dropped_points,
            voxel_count=self.voxel_count(),
            chunk_count=len(chunks),
            max_voxels=self.max_voxels,
            segments=self.segment_summary(),
        )
        payload.update(bounds)
        payload.update(extra)
        return payload


# ------------------------------------------------------------------------ odometry

def _keyframe_due(pose, keyframe_pose, timestamp, keyframe_time, cfg: dict) -> bool:
    delta = invert(keyframe_pose) @ pose
    if translation_m(delta) >= cfg["keyframe_translation"]:
        return True
    if rotation_deg(delta) >= cfg["keyframe_rotation"]:
        return True
    interval = cfg.get("keyframe_time")
    if interval and timestamp is not None and keyframe_time is not None and timestamp - keyframe_time >= interval:
        return True
    return False


def build_submap(keyframes: list, cfg: dict):
    """Accepted-frame window, expressed in the common map coordinate system."""
    if not keyframes:
        return None
    keep = keyframes[-int(cfg.get("submap_frames", cfg["submap_keyframes"])):]
    points = np.concatenate([transform(entry["cloud"], entry["pose"]) for entry in keep])
    intensity = np.concatenate([entry["intensity"] for entry in keep])
    xyz, _ = downsample(points, intensity, cfg.get("submap_voxel_size", cfg["voxel_size"]),
                        cfg.get("submap_max_points", 0))
    if len(xyz) == 0:
        return None
    target = dict(points=xyz, keyframes=[entry["keyframe_id"] for entry in keep])
    target.update(frame_count=len(keep), first_time=keep[0]["timestamp"], last_time=keep[-1]["timestamp"])
    target["tree"] = cKDTree(xyz)
    target["normals"] = (estimate_normals(xyz, cfg.get("normal_neighbors", 10))
                         if cfg.get("method", "point_to_point") == "point_to_plane" else None)
    return target


def _reinitialise(source, submap, previous_pose, velocity, cfg: dict):
    """Try several initial guesses after tracking loss.

    Re-initialisation is only allowed to succeed against the *last* submap, so a
    new segment still has a point-cloud constraint back to the previous one.
    """
    if submap is None:
        return None, dict(state="tracking_lost", reason="no_submap", overlap=0.0, rmse=None,
                          translation_step_m=0.0, rotation_step_m_deg=0.0, iterations=0,
                          method=cfg.get("method", "point_to_point"), point_count=int(len(source)))
    guesses = [previous_pose, previous_pose @ velocity, previous_pose @ velocity @ velocity, np.eye(4)]
    best = None
    for initial in guesses[:max(1, int(cfg.get("reinit_candidates", 4)))]:
        pose, quality = register(source, submap["points"], initial, cfg, submap["tree"], submap["normals"],
                                 reference=previous_pose)
        if pose is None:
            continue
        score = (quality["overlap"], -(quality["rmse"] or 0.0))
        if best is None or score > best[0]:
            best = (score, pose, quality)
    if best is None:
        return None, dict(state="tracking_lost", reason="reinit_failed", overlap=0.0, rmse=None,
                          translation_step_m=0.0, rotation_step_m_deg=0.0, iterations=0,
                          method=cfg.get("method", "point_to_point"), point_count=int(len(source)))
    return best[1], dict(best[2], state="reinitialized", reason="reinitialized")


def run_odometry(loader, timelines, cfg: dict, cancelled=lambda: False, progress=lambda *_: None):
    """Run the full odometry chain.

    ``loader(i)`` returns the raw ``(N, 4)`` frame; ``timelines[i]`` is the frame
    playback timestamp.  Returns a report dict whose ``map`` entry is the live
    :class:`VoxelMap` instance (serialise ``map.metadata()`` for JSON).
    """
    count = len(timelines)
    voxel_map = VoxelMap(cfg["map_voxel_size"], cfg.get("map_max_voxels", 0), cfg.get("chunk_voxels", 32))
    records: list = []
    keyframes: list = []
    accepted_clouds: list = []
    segments: list = []
    poses: list = []
    submap = None

    reference_pose = np.eye(4)
    pose = np.eye(4)
    pose_prev = None
    velocity = np.eye(4)
    keyframe_pose = np.eye(4)
    keyframe_time = None
    keyframe_id = -1
    previous_time = None
    accepted_time = None
    velocity_interval = None
    segment_id = -1
    lost = False
    failures = 0
    max_points = cfg["max_points"]
    bootstrap = cfg.get("initialization_mode") == "stationary_window"
    bootstrap_clouds = []
    initialization_failed = False

    for index in range(count):
        if cancelled():
            raise InterruptedError()
        timestamp = timelines[index]
        state = None
        reason = None
        quality = dict(overlap=0.0, rmse=None, translation_step_m=0.0, rotation_step_m_deg=0.0,
                       iterations=0, method=cfg.get("method", "point_to_point"))
        candidate = None
        keyframe_made = False
        prediction_reason = "not_attempted"
        preprocessing = {}

        invalid_time = (timestamp is None or not np.isfinite(timestamp)
                        or (previous_time is not None and timestamp <= previous_time))
        if invalid_time:
            source = np.empty((0, 3))
            intensity = np.empty((0,))
        else:
            source, intensity = preprocess(loader(index), cfg, preprocessing)
        diagnostic_target = submap
        statistics = spacing_statistics(transform(source, pose), diagnostic_target, cfg)

        initializing = bootstrap and index <= cfg["stationary_end_frame"]
        if initializing and not invalid_time and index >= cfg["stationary_start_frame"] and len(source):
            bootstrap_clouds.append((index, timestamp, source, intensity))
        if initializing and index == cfg["stationary_end_frame"]:
            if (not bootstrap_clouds or bootstrap_clouds[-1][1] - bootstrap_clouds[0][1]
                    > cfg["bootstrap_max_seconds"]):
                initialization_failed = True
            else:
                source, intensity = downsample(
                    np.concatenate([entry[2] for entry in bootstrap_clouds]),
                    np.concatenate([entry[3] for entry in bootstrap_clouds]),
                    cfg["submap_voxel_size"], cfg["submap_max_points"])
                initialization_failed = len(source) < cfg["min_points"] or is_planar(source, cfg)

        if invalid_time:
            state, reason = "timestamp_invalid", "timestamp_invalid"
        elif initialization_failed:
            state, reason = "tracking_lost", "initialization_failed"
        elif initializing and index < cfg["stationary_end_frame"]:
            state, reason = "insufficient_points", "initialization_pending"
        elif len(source) < cfg["min_points"] and segment_id >= 0 and not lost:
            state, reason = "insufficient_points", "too_few_points"
        elif segment_id < 0:
            # Reference frame: establishes the first-frame coordinate system.
            if len(source) < cfg["min_points"] or is_planar(source, cfg):
                state = "insufficient_points" if len(source) < cfg["min_points"] else "degenerate_geometry"
                reason = "invalid_reference"
            else:
                segment_id = 0
                segments.append(dict(segment_id=0, start_frame_index=index, end_frame_index=index,
                                     keyframe_ids=[], length_m=0.0, closure=None))
                pose, pose_prev, velocity = np.eye(4), None, np.eye(4)
                reference_pose = np.eye(4).copy()
                state, reason = "reference", "reference"
                lost = False
                if bootstrap:
                    reason = "stationary_assumption"
                    for old_index, old_time, old_source, old_intensity in bootstrap_clouds:
                        if old_index == index:
                            continue
                        old = records[old_index]
                        old.update(state="reference", reason="stationary_assumption", pose=np.eye(4).tolist(),
                                   segment_id=0, initialization_role="stationary_window")
                        poses[old_index].update(state="reference", reason="stationary_assumption",
                                                pose=np.eye(4), segment_id=0)
                    segments[-1]["start_frame_index"] = bootstrap_clouds[0][0]
                    bootstrap_clouds.clear()
                candidate = pose
                keyframe_id += 1
                keyframe_made = True
                entry = dict(keyframe_id=keyframe_id, frame_index=index, timestamp=timestamp, pose=pose.copy(),
                             cloud=source, intensity=intensity, quality=dict(quality),
                             submap_id=0, segment_id=segment_id, state=state)
                keyframes.append(entry)
                keyframe_pose, keyframe_time = pose.copy(), timestamp
        elif lost:
            candidate, quality = _reinitialise(source, submap, pose, velocity, cfg)
            if candidate is None:
                state, reason = "tracking_lost", quality.get("reason", "reinit_failed")
            else:
                state, reason = "reinitialized", "reinitialized"
        else:
            if len(source) < cfg["min_points"]:
                state, reason = "insufficient_points", "too_few_points"
            else:
                initial, prediction_reason = predict_pose(
                    pose, velocity, velocity_interval,
                    timestamp - accepted_time if accepted_time is not None else 0, cfg)
                candidate, quality = register(source, submap["points"], initial, cfg, submap["tree"],
                                              submap["normals"], reference=pose)
                state, reason = quality["state"], quality["reason"]
                if state == "accepted":
                    quality["state"] = "accepted"

        # ---- fusion and bookkeeping -------------------------------------
        if state in ("reference", "accepted", "reinitialized"):
            if state == "accepted":
                velocity = invert(pose) @ candidate
                velocity_interval = timestamp - accepted_time
                pose_prev = pose.copy()
                pose = candidate
                if segment_id < 0:
                    segment_id = 0
                    segments.append(dict(segment_id=0, start_frame_index=index, end_frame_index=index,
                                         keyframe_ids=[], length_m=0.0, closure=None))
                if _keyframe_due(pose, keyframe_pose, timestamp, keyframe_time, cfg):
                    keyframe_id += 1
                    keyframe_made = True
                    cloud, cloud_intensity = downsample(source, intensity, cfg["voxel_size"],
                                                        cfg.get("keyframe_max_points", max_points))
                    entry = dict(keyframe_id=keyframe_id, frame_index=index, timestamp=timestamp,
                                 pose=pose.copy(), cloud=cloud, intensity=cloud_intensity,
                                 quality=dict(quality), submap_id=segment_id, segment_id=segment_id,
                                 state=state)
                    keyframes.append(entry)
                    keyframe_pose, keyframe_time = pose.copy(), timestamp
            elif state == "reinitialized":
                pose = candidate
                pose_prev = None
                velocity = np.eye(4)
                velocity_interval = None
                lost = False
                segment_id += 1
                segments.append(dict(segment_id=segment_id, start_frame_index=index, end_frame_index=index,
                                     keyframe_ids=[], length_m=0.0, closure=None))
                keyframe_id += 1
                keyframe_made = True
                entry = dict(keyframe_id=keyframe_id, frame_index=index, timestamp=timestamp, pose=pose.copy(),
                             cloud=source, intensity=intensity, quality=dict(quality),
                             submap_id=segment_id, segment_id=segment_id, state=state)
                keyframes.append(entry)
                keyframe_pose, keyframe_time = pose.copy(), timestamp

            if segments:
                segments[-1]["end_frame_index"] = index
                if keyframe_made:
                    segments[-1]["keyframe_ids"].append(keyframe_id)

            fuse = state == "reference" or keyframe_made or cfg.get("fuse_non_keyframes", True)
            if fuse:
                voxel_map.add(transform(source, pose), intensity, timestamp, keyframe_id, max(segment_id, 0))
            accepted_time = timestamp
            accepted_clouds.append(dict(cloud=source, intensity=intensity, pose=pose.copy(),
                                        timestamp=timestamp, keyframe_id=keyframe_id))
            accepted_clouds = accepted_clouds[-int(cfg.get("submap_frames", cfg["submap_keyframes"])):]
            window = cfg.get("submap_seconds", 5.0)
            if window:
                accepted_clouds = [entry for entry in accepted_clouds if timestamp - entry["timestamp"] <= window]
            submap = build_submap(accepted_clouds, cfg)
            for entry in keyframes:
                entry.pop("cloud", None)
                entry.pop("intensity", None)

        if state == "accepted":
            if segments:
                previous = poses[-1] if poses and poses[-1]["pose"] is not None else None
                if previous is not None and previous["segment_id"] == segment_id:
                    segments[-1]["length_m"] += translation_m(invert(previous["pose"]) @ pose)

        # ---- tracking-loss state machine --------------------------------
        if state in ("reference", "accepted", "reinitialized"):
            failures = 0
        elif state in REJECTED_STATES and reason != "initialization_pending":
            # A single rejected frame is recoverable; a run of them means the
            # sensor can no longer be located and fusion must stop.
            failures += 1
            if failures >= int(cfg.get("lost_after_failures", 3)):
                state, reason = "tracking_lost", "tracking_lost_after_%d_failures" % failures
                lost = True
        if state == "tracking_lost":
            lost = True

        poses.append(dict(frame_index=index, timestamp=float(timestamp) if timestamp is not None and np.isfinite(timestamp) else None, state=state, reason=reason,
                          pose=None if candidate is None or state == "tracking_lost"
                          else np.asarray(candidate, dtype=np.float64),
                          candidate_pose=quality.get("candidate_pose"),
                          segment_id=segment_id, keyframe=keyframe_made))
        records.append(dict(
            index=index, timestamp=float(timestamp) if timestamp is not None and np.isfinite(timestamp) else None, state=state, reason=reason,
            initialization_role="stationary_window" if initializing else ("single_frame" if state == "reference" else None),
            overlap=quality.get("overlap"), rmse=quality.get("rmse"),
            translation_step_m=quality.get("translation_step_m"), rotation_step_deg=quality.get("rotation_step_m_deg"),
            iterations=quality.get("iterations"), point_count=int(len(source)),
            keyframe=keyframe_made, segment_id=segment_id,
            pose=None if candidate is None or state == "tracking_lost" else candidate.tolist(),
            candidate_pose=quality.get("candidate_pose"),
            prediction_reason=prediction_reason,
            preprocessing=preprocessing, spacing=statistics,
            normal_matrix_ratio=quality.get("normal_matrix_ratio"),
            submap_frame_count=diagnostic_target["frame_count"] if diagnostic_target else 0,
            submap_point_count=len(diagnostic_target["points"]) if diagnostic_target else 0,
        ))
        if not invalid_time:
            previous_time = timestamp
        progress(index + 1, count)

    for segment in segments:
        entries = [record for record in poses
                   if record["segment_id"] == segment["segment_id"] and record["pose"] is not None]
        if len(entries) >= 2:
            first = entries[0]["pose"]
            last = entries[-1]["pose"]
            closure = invert(first) @ last
            segment["closure"] = dict(translation_m=round(translation_m(closure), 5),
                                      rotation_deg=round(rotation_deg(closure), 5),
                                      first_frame_index=entries[0]["frame_index"],
                                      last_frame_index=entries[-1]["frame_index"])
        segment["length_m"] = round(segment["length_m"], 5)

    trajectory_frames = [{**entry, "pose": None if entry["pose"] is None else entry["pose"].tolist()}
                         for entry in poses]
    metrics = summarise(records, segments, voxel_map)
    metrics["keyframe_count"] = len(keyframes)
    metrics["initialization_failed"] = initialization_failed or (bootstrap and segment_id < 0)
    for segment in segments:
        segment["map_id"] = 0
        segment["coordinate_relation"] = "same_map_geometric_recovery" if segment["segment_id"] else "reference"
    for record in records:
        record["map_id"] = 0
    for record in trajectory_frames:
        record["map_id"] = 0
    return dict(version=ODOMETRY_VERSION, stage=STAGE, imu_used=False, loop_closure_used=False,
                config=cfg, config_version=configuration_version(cfg), frame_count=count,
                frames=records,
                trajectory=dict(version=ODOMETRY_VERSION, segments=segments, frames=trajectory_frames),
                keyframes=[{**{key: value for key, value in entry.items()
                               if key not in ("cloud", "intensity")},
                            "pose": entry["pose"].tolist()} for entry in keyframes],
                map=voxel_map, metrics=metrics)


def summarise(records: list, segments: list, voxel_map: VoxelMap) -> dict:
    """Derive the acceptance metrics from per-frame records."""
    accepted = [r for r in records if r["state"] in ("reference", "accepted", "reinitialized")]
    rejected = [r for r in records if r["state"] in REJECTED_STATES]
    lost = [r for r in records if r["state"] == "tracking_lost"]
    overlaps = [r["overlap"] for r in records if r["state"] != "reference" and r["overlap"] is not None]
    rmses = [r["rmse"] for r in records if r["rmse"] is not None]
    steps = [r["translation_step_m"] for r in records if r["translation_step_m"]]
    rotations = [r["rotation_step_deg"] for r in records if r["rotation_step_deg"]]

    longest, run_start = 0.0, None
    for record in records:
        if record["state"] in ("reference", "accepted", "reinitialized"):
            if run_start is None:
                run_start = record["timestamp"]
            elif record["timestamp"] is not None:
                longest = max(longest, float(record["timestamp"] - run_start))
        else:
            run_start = None

    closure = None
    for segment in segments:
        if segment["closure"] is not None:
            closure = segment["closure"]
            break

    return dict(
        frame_count=len(records),
        initialization_frames=sum(r["state"] == "reference" for r in records),
        registered_frames=sum(r["state"] == "accepted" for r in records),
        recovered_frames=sum(r["state"] == "reinitialized" for r in records),
        total_failure_ratio=round((len(rejected) + len(lost)) / len(records), 4) if records else None,
        accepted_frames=len(accepted),
        rejected_frames=len(rejected),
        tracking_lost_frames=len(lost),
        keyframe_count=None,  # filled from the keyframe list by the caller
        accepted_ratio=round(len(accepted) / len(records), 4) if records else 0.0,
        rejected_ratio=round(len(rejected) / len(records), 4) if records else 0.0,
        first_tracking_lost_time=lost[0]["timestamp"] if lost else None,
        max_continuous_tracking_s=round(longest, 4),
        mean_overlap=round(float(np.mean(overlaps)), 4) if overlaps else None,
        mean_rmse_m=round(float(np.mean(rmses)), 5) if rmses else None,
        max_translation_step_m=round(float(max(steps)), 5) if steps else None,
        max_rotation_step_deg=round(float(max(rotations)), 5) if rotations else None,
        trajectory_length_m=round(sum(segment["length_m"] for segment in segments), 5),
        closure_translation_m=closure["translation_m"] if closure else None,
        closure_rotation_deg=closure["rotation_deg"] if closure else None,
        segment_count=len(segments),
        map_point_count=voxel_map.fused_points,
        map_voxel_count=voxel_map.voxel_count(),
        map_bounds=voxel_map.bounds(),
    )
