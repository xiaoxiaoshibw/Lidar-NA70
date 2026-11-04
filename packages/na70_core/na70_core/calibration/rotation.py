"""LiDAR-to-IMU rotation and time offset from angular-rate correspondence.

Two clocks, one rigid rig:

    omega_imu(t) = R_IL @ omega_lidar(t - tau) + b_g

Given LiDAR angular rates from scan registration (or a synthetic truth) and
IMU gyro rates, the rotation is a Wahba problem and ``tau`` is found by a
bounded grid search.  Translation ``t_IL`` is *not* observable from rotation
alone: a lever arm only appears through linear acceleration, so this module
never fabricates it.

Observability is reported from the eigenvalue spread of the LiDAR angular
velocity scatter.  Motion dominated by one axis leaves the rotation about that
axis weakly constrained; the result is then downgraded instead of presented as
a full extrinsic.
"""
from __future__ import annotations

import math

import numpy as np


LIMITATIONS = (
    "只从角速度对齐求 R_IL 与时间偏移；t_IL 不可由纯旋转观测",
    "LiDAR 角速度必须来自可追溯的配准/真值；本模块不生成它",
    "弱单一轴运动时绕该轴的旋转不可观测，结果必须降级",
    "时间偏移是离散搜索值，受网格、LiDAR 更新率和两路时钟漂移限制",
)

_EPS = 1e-12


def _series(times, values, name):
    times = np.asarray(times, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    if times.ndim != 1 or values.ndim != 2 or values.shape != (len(times), 3):
        raise ValueError(f"{name} must be 1-D times and matching Nx3 values")
    if len(times) < 4:
        raise ValueError(f"{name} needs at least four samples")
    if not np.isfinite(times).all() or not np.isfinite(values).all():
        raise ValueError(f"{name} contains non-finite values")
    if np.any(np.diff(times) <= 0):
        raise ValueError(f"{name} times must strictly increase")
    return times, values


def wahba_rotation(source, target, weights=None):
    """Return ``R`` minimizing ``sum w |target - R source|^2``."""
    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("source and target must be matching Nx3 arrays")
    if len(source) < 3:
        raise ValueError("at least three vector pairs are required")
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("vector pairs contain non-finite values")
    if weights is None:
        weight = np.ones(len(source), dtype=np.float64)
    else:
        weight = np.asarray(weights, dtype=np.float64)
        if weight.shape != (len(source),) or not np.isfinite(weight).all() or np.any(weight < 0):
            raise ValueError("weights must be finite non-negative N-vector")
        if weight.sum() <= 0:
            raise ValueError("weights sum to zero")
    weight = weight / weight.sum()
    covariance = (source * weight[:, None]).T @ target
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T @ u.T
    return rotation


def rotation_matrix_to_quaternion(matrix):
    """Return ``(w, x, y, z)`` for a proper rotation matrix."""
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("matrix must be a finite 3x3")
    if not np.isclose(np.linalg.det(matrix), 1.0, atol=1e-6):
        raise ValueError("matrix is not a proper rotation")
    trace = float(np.trace(matrix))
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w = 0.25 * s
        x = (matrix[2, 1] - matrix[1, 2]) / s
        y = (matrix[0, 2] - matrix[2, 0]) / s
        z = (matrix[1, 0] - matrix[0, 1]) / s
    elif matrix[0, 0] > matrix[1, 1] and matrix[0, 0] > matrix[2, 2]:
        s = math.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2]) * 2
        w = (matrix[2, 1] - matrix[1, 2]) / s
        x = 0.25 * s
        y = (matrix[0, 1] + matrix[1, 0]) / s
        z = (matrix[0, 2] + matrix[2, 0]) / s
    elif matrix[1, 1] > matrix[2, 2]:
        s = math.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2]) * 2
        w = (matrix[0, 2] - matrix[2, 0]) / s
        x = (matrix[0, 1] + matrix[1, 0]) / s
        y = 0.25 * s
        z = (matrix[1, 2] + matrix[2, 1]) / s
    else:
        s = math.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1]) * 2
        w = (matrix[1, 0] - matrix[0, 1]) / s
        x = (matrix[0, 2] + matrix[2, 0]) / s
        y = (matrix[1, 2] + matrix[2, 1]) / s
        z = 0.25 * s
    quaternion = np.array([w, x, y, z], dtype=np.float64)
    quaternion /= np.linalg.norm(quaternion)
    if quaternion[0] < 0:
        quaternion = -quaternion
    return quaternion


def quaternion_wxyz(matrix):
    return rotation_matrix_to_quaternion(matrix).tolist()


def rotation_matrix_to_euler_zyx_deg(matrix):
    """Intrinsic Z-Y-X (yaw, pitch, roll) in degrees for diagnostics only."""
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (3, 3):
        raise ValueError("matrix must be 3x3")
    sy = -matrix[2, 0]
    sy = float(np.clip(sy, -1.0, 1.0))
    pitch = math.asin(sy)
    if abs(sy) < 1 - 1e-9:
        yaw = math.atan2(matrix[1, 0], matrix[0, 0])
        roll = math.atan2(matrix[2, 1], matrix[2, 2])
    else:
        yaw = math.atan2(-matrix[0, 1], matrix[1, 1])
        roll = 0.0
    return np.degrees([yaw, pitch, roll]).tolist()


def _interpolate(times, values, query):
    result = np.empty((len(query), 3), dtype=np.float64)
    for axis in range(3):
        result[:, axis] = np.interp(query, times, values[:, axis])
    return result


def _fit_at_offset(t_lidar, omega_lidar, times, omega_imu, offset):
    query = times - offset
    valid = (query >= t_lidar[0]) & (query <= t_lidar[-1])
    if int(valid.sum()) < 20:
        return None
    sample = _interpolate(t_lidar, omega_lidar, query[valid])
    target = omega_imu[valid]
    rotation = wahba_rotation(sample, target)
    residual = target - sample @ rotation.T
    bias = residual.mean(axis=0)
    residual = residual - bias
    rms = float(np.sqrt(np.mean(np.sum(residual ** 2, axis=1))))
    reference = float(np.sqrt(np.mean(np.sum(target ** 2, axis=1)))) + _EPS
    return {"rotation": rotation, "rms": rms, "bias": bias, "relative": rms / reference,
            "count": int(valid.sum())}


def _observability(omega_lidar, omega_imu, rotation):
    sample = np.asarray(omega_lidar, dtype=np.float64)
    target = np.asarray(omega_imu, dtype=np.float64)
    weight = np.linalg.norm(sample, axis=1)
    total = weight.sum()
    if total <= _EPS:
        return {"status": "NO_ROTATION", "axis_coverage": 0.0, "eigenvalues": [0, 0, 0]}
    normalized = sample / np.maximum(weight[:, None], _EPS)
    scatter = (normalized * weight[:, None]).T @ normalized / total
    eigenvalues = np.sort(np.linalg.eigvalsh(scatter))[::-1]
    coverage = float(eigenvalues[-1] / max(eigenvalues[0], _EPS))
    aligned = target - sample @ rotation.T
    return {
        "status": "THREE_AXIS" if coverage >= 0.02 else "WEAKLY_OBSERVABLE",
        "axis_coverage": coverage,
        "eigenvalues": eigenvalues.tolist(),
        "mean_speed_lidar": float(np.linalg.norm(sample, axis=1).mean()),
        "mean_speed_imu": float(np.linalg.norm(target, axis=1).mean()),
        "relative_residual": float(np.sqrt(np.mean(np.sum(aligned ** 2, axis=1)))
                                   / (np.sqrt(np.mean(np.sum(target ** 2, axis=1))) + _EPS)),
    }


def estimate_time_offset(t_lidar, omega_lidar, times, omega_imu, *,
                         max_offset_s=0.5, coarse_step_s=0.002, fine_step_s=0.0001):
    """Search ``tau`` in ``[-max_offset_s, max_offset_s]``.

    Model: ``omega_imu(t) = R omega_lidar(t - tau) + b``.  Returns the best
    grid point plus a one-step parabolic refinement; the reported resolution is
    the coarse/fine grid spacing, not a statistical confidence interval.
    """
    t_lidar, omega_lidar = _series(t_lidar, omega_lidar, "lidar")
    times, omega_imu = _series(times, omega_imu, "imu")
    for name, value in (("max_offset_s", max_offset_s), ("coarse_step_s", coarse_step_s),
                        ("fine_step_s", fine_step_s)):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if fine_step_s > coarse_step_s:
        raise ValueError("fine_step_s must not exceed coarse_step_s")
    grid = np.arange(-max_offset_s, max_offset_s + coarse_step_s * 0.5, coarse_step_s)
    scored = []
    for offset in grid:
        fit = _fit_at_offset(t_lidar, omega_lidar, times, omega_imu, float(offset))
        if fit is not None:
            scored.append((float(offset), fit))
    if not scored:
        raise ValueError("no overlapping samples for any candidate offset")
    best_offset, best_fit = min(scored, key=lambda item: item[1]["rms"])
    fine = np.arange(best_offset - coarse_step_s, best_offset + coarse_step_s + fine_step_s * 0.5,
                     fine_step_s)
    fine_scored = []
    for offset in fine:
        fit = _fit_at_offset(t_lidar, omega_lidar, times, omega_imu, float(offset))
        if fit is not None:
            fine_scored.append((float(offset), fit))
    if fine_scored:
        best_offset, best_fit = min(fine_scored, key=lambda item: item[1]["rms"])
    return {
        "time_offset_s": float(best_offset),
        "resolution_s": float(fine_step_s),
        "rotation": best_fit["rotation"],
        "residual_rms": best_fit["rms"],
        "residual_relative": best_fit["relative"],
        "gyro_bias_in_imu_frame": best_fit["bias"],
        "matched_samples": best_fit["count"],
        "search_range_s": float(max_offset_s),
        "coarse_step_s": float(coarse_step_s),
    }


def solve_lidar_imu_rotation(t_lidar, omega_lidar, times, omega_imu, *,
                             max_offset_s=0.5, coarse_step_s=0.002, fine_step_s=0.0001,
                             max_relative_residual=0.35):
    """Full rotation/time-offset candidate with observability and status."""
    fit = estimate_time_offset(t_lidar, omega_lidar, times, omega_imu,
                               max_offset_s=max_offset_s, coarse_step_s=coarse_step_s,
                               fine_step_s=fine_step_s)
    rotation = fit["rotation"]
    query = times - fit["time_offset_s"]
    valid = (query >= np.min(t_lidar)) & (query <= np.max(t_lidar))
    lidar_sample = _interpolate(np.asarray(t_lidar), np.asarray(omega_lidar), query[valid])
    observability = _observability(lidar_sample, np.asarray(omega_imu)[valid], rotation)
    reasons = []
    if observability["status"] != "THREE_AXIS":
        reasons.append("weak_rotation_observability")
    if fit["residual_relative"] > max_relative_residual:
        reasons.append("residual_exceeds_limit")
    status = "SOLVED_CANDIDATE" if not reasons else (
        "INSUFFICIENT_MOTION" if "weak_rotation_observability" in reasons else "POOR_FIT")
    return {
        "schema": "na70-lidar-imu-rotation-v1",
        "status": status,
        "reasons": reasons,
        "R_imu_lidar": rotation.tolist(),
        "quaternion_wxyz": quaternion_wxyz(rotation),
        "euler_zyx_deg": rotation_matrix_to_euler_zyx_deg(rotation),
        "time_offset_s": fit["time_offset_s"],
        "time_offset_resolution_s": fit["resolution_s"],
        "residual": {
            "rms": fit["residual_rms"],
            "relative": fit["residual_relative"],
            "matched_samples": fit["matched_samples"],
            "limit_relative": float(max_relative_residual),
        },
        "observability": observability,
        "gyro_bias_in_imu_frame": fit["gyro_bias_in_imu_frame"].tolist(),
        "search": {"range_s": fit["search_range_s"], "coarse_step_s": fit["coarse_step_s"]},
        "limitations": list(LIMITATIONS),
    }