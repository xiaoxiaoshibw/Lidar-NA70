"""Planar-target measurements for NA70 range and angular consistency.

A wall or flat target gives points that should lie on one plane.  The fitted
normal distance is ``n . centroid`` with ``n`` oriented away from the sensor.
Comparing it to an independently measured reference distance tests the *total*
range chain (LiDAR origin + decoded range), so a residual intercept cannot be
called a range bias until the optical origin is known.

Low plane residuals prove the surface is flat in the point cloud; they do not
prove absolute accuracy.  Absolute scale/offset needs at least three
independent reference distances, and the reference instrument's uncertainty
must be reported alongside.
"""
from __future__ import annotations

import math

import numpy as np


LIMITATIONS = (
    "平面残差低只说明点云自洽，不证明绝对测距或角度正确",
    "法向距离包含光学原点未知量；原点未测时不能把截距称为测距零偏",
    "距离/角度真值需独立基准，并随结果记录其精度",
    "拟合与留出数据必须分开；同组数据残差变小不构成验收",
)


def _points(values):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("points must be an Nx3 array")
    if len(array) < 3:
        raise ValueError("at least three points are required")
    if not np.isfinite(array).all():
        raise ValueError("points contain non-finite values")
    return array


def fit_plane(points, *, max_residual_m=0.05, iterations=8):
    """Deterministic trimmed plane fit; returns a JSON-ready report."""
    array = _points(points)
    threshold = float(max_residual_m)
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("max_residual_m must be finite and positive")
    if not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    inliers = np.ones(len(array), dtype=bool)
    centroid = array.mean(axis=0)
    normal = np.array([0.0, 0.0, 1.0])
    for _ in range(iterations):
        subset = array[inliers]
        centroid = subset.mean(axis=0)
        _, _, vt = np.linalg.svd(subset - centroid, full_matrices=False)
        normal = vt[-1]
        if np.dot(normal, centroid) < 0:
            normal = -normal
        residual = (array - centroid) @ normal
        scale = 1.4826 * np.median(np.abs(residual - np.median(residual))) + 1e-9
        limit = max(threshold, 3.0 * scale)
        updated = np.abs(residual) <= limit
        if np.array_equal(updated, inliers) or updated.sum() < 3:
            break
        inliers = updated
    subset = array[inliers]
    centroid = subset.mean(axis=0)
    _, singular, vt = np.linalg.svd(subset - centroid, full_matrices=False)
    normal = vt[-1]
    if np.dot(normal, centroid) < 0:
        normal = -normal
    residual = (array - centroid) @ normal
    inlier_residual = residual[inliers]
    rms = float(np.sqrt(np.mean(inlier_residual ** 2)))
    p95 = float(np.percentile(np.abs(inlier_residual), 95))
    distance = float(np.dot(normal, centroid))
    tangent = float(np.sqrt(max(singular[1] ** 2 - singular[2] ** 2, 0.0)) / max(len(subset), 1))
    return {
        "schema": "na70-lidar-plane-v1",
        "status": "FIT" if inliers.sum() >= 3 else "INSUFFICIENT_INLIERS",
        "point_count": int(len(array)),
        "inlier_count": int(inliers.sum()),
        "inlier_ratio": float(inliers.sum() / len(array)),
        "centroid_m": centroid.tolist(),
        "normal_unit": normal.tolist(),
        "normal_distance_m": distance,
        "residual_rms_m": rms,
        "residual_p95_m": p95,
        "tangent_span_m": tangent,
        "inlier_mask": inliers.tolist(),
        "limitations": list(LIMITATIONS),
    }


def plane_measurement(points, *, reference_distance_m=None, origin_offset_m=0.0,
                      max_residual_m=0.05):
    """Plane fit plus the residual against an independent reference distance."""
    result = fit_plane(points, max_residual_m=max_residual_m)
    measured = result["normal_distance_m"]
    result["reference"] = None
    if reference_distance_m is not None:
        reference = float(reference_distance_m)
        offset = float(origin_offset_m)
        if not math.isfinite(reference) or reference <= 0:
            raise ValueError("reference_distance_m must be finite and positive")
        if not math.isfinite(offset):
            raise ValueError("origin_offset_m must be finite")
        expected = reference + offset
        error = measured - expected
        result["reference"] = {
            "reference_distance_m": reference,
            "origin_offset_m": offset,
            "expected_distance_m": expected,
            "measured_distance_m": measured,
            "error_m": error,
            "relative_error": error / expected,
        }
    return result


def multi_range_fit(reference_distances, measured_distances, *, min_points=3):
    """Least-squares ``measured = slope * reference + intercept``.

    Fewer than ``min_points`` distances return ``NEEDS_MORE_DATA`` rather than
    an extrapolated two-point line presented as calibration.
    """
    reference = np.asarray(reference_distances, dtype=np.float64)
    measured = np.asarray(measured_distances, dtype=np.float64)
    if reference.ndim != 1 or measured.ndim != 1 or len(reference) != len(measured):
        raise ValueError("reference and measured distances must be matching 1-D arrays")
    if len(reference) < 2:
        raise ValueError("at least two distances are required")
    if not np.isfinite(reference).all() or not np.isfinite(measured).all():
        raise ValueError("distances contain non-finite values")
    if np.any(reference <= 0):
        raise ValueError("reference distances must be positive")
    design = np.column_stack([reference, np.ones(len(reference))])
    solution, residuals, rank, _ = np.linalg.lstsq(design, measured, rcond=None)
    slope, intercept = float(solution[0]), float(solution[1])
    predicted = design @ solution
    residual = measured - predicted
    dof = max(len(reference) - 2, 1)
    variance = float(np.sum(residual ** 2) / dof)
    covariance = variance * np.linalg.inv(design.T @ design)
    slope_se = float(math.sqrt(max(covariance[0, 0], 0.0)))
    intercept_se = float(math.sqrt(max(covariance[1, 1], 0.0)))
    status = "FIT" if len(reference) >= min_points and rank == 2 else "NEEDS_MORE_DATA"
    return {
        "schema": "na70-lidar-range-fit-v1",
        "status": status,
        "count": int(len(reference)),
        "slope": slope,
        "intercept_m": intercept,
        "slope_standard_error": slope_se,
        "intercept_standard_error_m": intercept_se,
        "residual_rms_m": float(np.sqrt(np.mean(residual ** 2))),
        "residuals_m": residual.tolist(),
        "note": "截距混合光学原点与真实测距零偏；原点未独立测量时不得单独解释为测距零偏。",
        "limitations": list(LIMITATIONS),
    }