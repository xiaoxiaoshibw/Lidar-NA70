"""Plane-based LiDAR-to-IMU rotation from a static/moving wall sequence.

For each selected LiDAR frame, a dominant plane normal ``n_L`` is fitted in the
LiDAR frame.  The device quaternion gives ``R_WI``.  For the same physical plane
the world normal is constant:

    n_W = R_WI(t) @ R_IL @ n_L(t)

Given normals and quaternions over a slow rotation, this solves ``R_IL`` and the
common ``n_W``.  A grid search over a time offset ``tau`` (using ``R_WI(t-tau)``)
also estimates the LiDAR-IMU time offset.

This needs a dominant flat surface.  A corner with two equally large walls can
make the dominant plane switch and invalidate the correspondences.  The result
is a rotation candidate only: translation and absolute accuracy are not solved.
"""
from __future__ import annotations

import math

import numpy as np


LIMITATIONS = (
    "需要一面主导平面；墙角两块等大墙面会让主导平面跳变",
    "四元数是设备融合姿态，可能被平滑，不能当独立角速度基准",
    "只解 R_IL 与时间偏移；平移仍需机械测量",
    "平面法向符号必须一致朝向传感器；错误配对会产生低残差假解",
    "结果需在留出转动/墙面上验证，不能只看残差小",
)


def _points(values):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] < 3 or len(array) < 3:
        raise ValueError("points must be a non-empty Nx3+ array")
    if not np.isfinite(array[:, :3]).all():
        raise ValueError("points contain non-finite values")
    return array[:, :3]


def ransac_plane(points, *, threshold_m=0.05, iterations=200, seed=0):
    """Deterministic-seed RANSAC plane fit, then SVD refinement."""
    data = _points(points)
    if not math.isfinite(float(threshold_m)) or threshold_m <= 0:
        raise ValueError("threshold_m must be finite and positive")
    if not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    generator = np.random.default_rng(seed)
    best_mask = None
    best_count = 0
    for _ in range(iterations):
        sample = data[generator.choice(len(data), 3, replace=False)]
        normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
        norm = np.linalg.norm(normal)
        if norm <= 1e-12:
            continue
        normal /= norm
        distance = np.abs((data - sample[0]) @ normal)
        mask = distance <= threshold_m
        count = int(mask.sum())
        if count > best_count:
            best_count, best_mask = count, mask
    if best_mask is None or best_count < 3:
        raise ValueError("RANSAC found no plane")
    subset = data[best_mask]
    centroid = subset.mean(axis=0)
    _, _, vt = np.linalg.svd(subset - centroid, full_matrices=False)
    normal = vt[-1]
    if np.dot(normal, centroid) < 0:
        normal = -normal
    distance = np.abs((data - centroid) @ normal)
    inliers = distance <= threshold_m
    if int(inliers.sum()) >= 3:
        subset = data[inliers]
        centroid = subset.mean(axis=0)
        _, _, vt = np.linalg.svd(subset - centroid, full_matrices=False)
        normal = vt[-1]
        if np.dot(normal, centroid) < 0:
            normal = -normal
        distance = np.abs((data - centroid) @ normal)
        inliers = distance <= threshold_m
    residual = distance[inliers]
    return {
        "normal": normal,
        "centroid": centroid,
        "inlier_ratio": float(inliers.mean()),
        "inlier_count": int(inliers.sum()),
        "residual_rms_m": float(np.sqrt(np.mean(residual ** 2))),
        "residual_p95_m": float(np.percentile(residual, 95)),
        "mask": inliers,
    }


def _unit_angles(normal):
    normal = normal / np.linalg.norm(normal)
    theta = math.asin(float(np.clip(normal[2], -1.0, 1.0)))
    phi = math.atan2(float(normal[1]), float(normal[0]))
    return np.array([theta, phi])


def _from_angles(angles):
    theta, phi = float(angles[0]), float(angles[1])
    return np.array([math.cos(theta) * math.cos(phi),
                     math.cos(theta) * math.sin(phi), math.sin(theta)])


def solve_rotation(normals_lidar, rotations_world_imu, *, max_iterations=200,
                   max_rms_deg=1.0):
    """Solve ``R_IL`` and the common world normal from plane normals.

    ``rotations_world_imu[i]`` is ``R_WI`` at the frame time.  Returns a report
    with the rotation matrix, quaternion, residual and observability.
    """
    normals = np.asarray(normals_lidar, dtype=np.float64)
    rotations = np.asarray(rotations_world_imu, dtype=np.float64)
    if normals.ndim != 2 or normals.shape[1] != 3 or len(normals) < 4:
        raise ValueError("at least four plane normals are required")
    if rotations.shape != (len(normals), 3, 3):
        raise ValueError("one 3x3 R_WI is required per normal")
    if not (np.isfinite(normals).all() and np.isfinite(rotations).all()):
        raise ValueError("non-finite input")
    if not math.isfinite(float(max_rms_deg)) or max_rms_deg <= 0:
        raise ValueError("max_rms_deg must be finite and positive")
    normals = normals / np.linalg.norm(normals, axis=1, keepdims=True)
    try:
        from scipy.optimize import least_squares
        from scipy.spatial.transform import Rotation
    except ImportError as exc:  # pragma: no cover - venv has scipy
        raise RuntimeError("plane extrinsic solver requires scipy") from exc

    def predict(x):
        rotation = Rotation.from_rotvec(x[:3]).as_matrix()
        world = _from_angles(x[3:])
        rotated = np.einsum("nij,nj->ni", rotations, normals @ rotation.T)
        return rotated, world

    def residual(x):
        rotated, world = predict(x)
        return (rotated - world).ravel()

    initial_world = np.mean(np.einsum("nij,nj->ni", rotations, normals), axis=0)
    world_angles = _unit_angles(initial_world)
    best = None
    for seed in (np.zeros(3), np.array([math.pi, 0.0, 0.0]),
                 np.array([0.0, 0.0, math.pi]), np.array([0.0, math.pi, 0.0])):
        initial = np.concatenate([seed, world_angles])
        solution = least_squares(residual, initial, max_nfev=max_iterations)
        cost = float(np.sqrt(np.mean(solution.fun ** 2)))
        if best is None or cost < best[0]:
            best = (cost, solution.x)
    solution_x = best[1]
    rotated, world = predict(solution_x)
    cosine = np.clip(np.sum(rotated * world, axis=1), -1.0, 1.0)
    angles = np.degrees(np.arccos(cosine))
    rotation = Rotation.from_rotvec(solution_x[:3]).as_matrix()
    quaternion = Rotation.from_matrix(rotation).as_quat()  # x,y,z,w
    scatter = normals.T @ normals / len(normals)
    eigenvalues = np.sort(np.linalg.eigvalsh(scatter))[::-1]
    coverage = float(eigenvalues[-1] / max(eigenvalues[0], 1e-12))
    rms = float(np.sqrt(np.mean(angles ** 2)))
    reasons = []
    if coverage < 0.02:
        reasons.append("weak_rotation_observability")
    if rms > max_rms_deg:
        reasons.append("residual_exceeds_limit")
    status = ("SOLVED_CANDIDATE" if not reasons else
              ("INSUFFICIENT_MOTION" if "weak_rotation_observability" in reasons else "POOR_FIT"))
    return {
        "schema": "na70-plane-extrinsic-v1",
        "status": status,
        "reasons": reasons,
        "R_imu_lidar": rotation.tolist(),
        "quaternion_xyzw": quaternion.tolist(),
        "world_normal": (world / np.linalg.norm(world)).tolist(),
        "residual": {
            "rms_deg": rms,
            "max_deg": float(angles.max()),
            "median_deg": float(np.median(angles)),
            "limit_deg": float(max_rms_deg),
        },
        "observability": {
            "normal_scatter_eigenvalues": eigenvalues.tolist(),
            "axis_coverage": coverage,
            "status": "THREE_AXIS" if coverage >= 0.02 else "WEAKLY_OBSERVABLE",
        },
        "samples": int(len(normals)),
        "limitations": list(LIMITATIONS),
        "claims": {"rotation_candidate_only": True, "translation_solved": False,
                   "holdout_validated": False},
    }


def interpolate_rotations(times, quaternions_wxyz, query_times):
    """SLERP device quaternions at query times (clamped to the recorded span)."""
    times = np.asarray(times, dtype=np.float64)
    quaternions = np.asarray(quaternions_wxyz, dtype=np.float64)
    query = np.asarray(query_times, dtype=np.float64)
    if times.ndim != 1 or quaternions.shape != (len(times), 4) or query.ndim != 1:
        raise ValueError("times, wxyz quaternions and query times must align")
    if np.any(np.diff(times) < 0):
        raise ValueError("IMU times must be non-decreasing")
    try:
        from scipy.spatial.transform import Rotation, Slerp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("quaternion interpolation requires scipy") from exc
    unique, index = np.unique(times, return_index=True)
    if len(unique) < 2:
        raise ValueError("at least two distinct IMU times are required")
    quaternions = quaternions[index]
    quaternions = quaternions / np.linalg.norm(quaternions, axis=1, keepdims=True)
    # scipy uses xyzw.
    rotations = Rotation.from_quat(quaternions[:, [1, 2, 3, 0]])
    clipped = np.clip(query, unique[0], unique[-1])
    return Slerp(unique, rotations)(clipped).as_matrix()