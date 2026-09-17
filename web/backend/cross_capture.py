"""Validated SE(2) alignment for diagnostic cross-capture submaps.

This module does not bypass the mapping-job gate.  It determines whether two
already accumulated captures behave like rigid static submaps before a stitch
preview is allowed to claim geometric support.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize
from scipy.spatial import cKDTree

CROSS_CAPTURE_VERSION = "na70-cross-capture-validation-v1"


def rotate_xy(points, yaw_deg):
    angle = math.radians(float(yaw_deg))
    cosine, sine = math.cos(angle), math.sin(angle)
    rotation = np.array([[cosine, -sine], [sine, cosine]])
    return np.asarray(points, dtype=np.float64) @ rotation.T


def deterministic_sample(points, maximum):
    points = np.asarray(points, dtype=np.float64)
    if len(points) <= maximum:
        return points
    return points[np.linspace(0, len(points) - 1, maximum, dtype=np.int64)]


def rigid_band(points, minimum_height=0.5, maximum_height=4.5):
    """Select a conservative non-ground band; this is geometry, not semantics."""
    points = np.asarray(points, dtype=np.float64)
    if not len(points):
        return points
    ground = float(np.median(points[:, 2]))
    height = points[:, 2] - ground
    return points[(height > minimum_height) & (height < maximum_height)]


def _trimmed_mean(distances, trim):
    distances = np.asarray(distances, dtype=np.float64)
    if not len(distances):
        return float("inf")
    cutoff = np.quantile(distances, trim)
    retained = distances[distances <= cutoff]
    return float(np.mean(retained)) if len(retained) else float("inf")


def _transform(points, yaw, tx, ty):
    return rotate_xy(points, yaw) + np.array([tx, ty])


def _voxel_xy(points, resolution=0.25):
    points = np.asarray(points, dtype=np.float64)
    keys = np.floor(points / resolution).astype(np.int64)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    counts = np.bincount(inverse).astype(float)
    return np.column_stack([
        np.bincount(inverse, weights=points[:, axis]) / counts for axis in range(2)
    ])


def _metrics(source, reference, transform, distances=(0.3, 0.5, 1.0)):
    moved = _transform(source, transform[0], transform[1], transform[2])
    source_to_reference = cKDTree(reference).query(moved, k=1)[0]
    reference_to_source = cKDTree(moved).query(reference, k=1)[0]
    combined = np.concatenate((source_to_reference, reference_to_source))
    return dict(
        median_m=float(np.median(combined)),
        p95_m=float(np.percentile(combined, 95)),
        symmetric_overlap={
            str(distance): float(((source_to_reference <= distance).mean()
                                  + (reference_to_source <= distance).mean()) / 2)
            for distance in distances
        },
        source_to_reference_median_m=float(np.median(source_to_reference)),
        reference_to_source_median_m=float(np.median(reference_to_source)),
    )


def _forward_metrics(source, reference, transform, distances=(0.3, 0.5, 1.0)):
    moved = _transform(source, transform[0], transform[1], transform[2])
    values = cKDTree(reference).query(moved, k=1)[0]
    return dict(
        median_m=float(np.median(values)),
        p95_m=float(np.percentile(values, 95)),
        overlap={str(distance): float((values <= distance).mean()) for distance in distances},
    )


def align_static_submaps(source_xyz, reference_xyz, yaw_bounds=(-80.0, 80.0),
                         yaw_step=0.5, max_translation=1.0, trim=0.85,
                         fit_points=20_000, evaluation_points=20_000):
    """Fit on one deterministic split and report symmetric held-out metrics."""
    source = _voxel_xy(rigid_band(source_xyz)[:, :2])
    reference = _voxel_xy(rigid_band(reference_xyz)[:, :2])
    if len(source) < 20 or len(reference) < 20:
        raise ValueError("跨采集配准缺少足够的非地面结构点")
    source_fit = deterministic_sample(source[np.arange(len(source)) % 5 != 0], fit_points)
    reference_fit = deterministic_sample(reference, fit_points)
    source_eval = deterministic_sample(source[np.arange(len(source)) % 5 == 0], evaluation_points)
    reference_eval = deterministic_sample(reference, evaluation_points)
    tree = cKDTree(reference_fit)

    reference_centre = np.median(reference_fit, axis=0)

    def yaw_seed(yaw):
        rotated = rotate_xy(source_fit, yaw)
        translation = reference_centre - np.median(rotated, axis=0)
        distances = tree.query(rotated + translation, k=1)[0]
        return _trimmed_mean(distances, trim), translation

    candidates = np.arange(yaw_bounds[0], yaw_bounds[1] + yaw_step * 0.5, yaw_step)
    seeded = [yaw_seed(yaw) for yaw in candidates]
    scores = np.array([item[0] for item in seeded])
    best_index = int(np.argmin(scores))
    best_yaw = float(candidates[best_index])
    best_translation = np.clip(seeded[best_index][1], -max_translation, max_translation)

    def objective(parameters):
        yaw, tx, ty = parameters
        if (yaw < yaw_bounds[0] or yaw > yaw_bounds[1]
                or abs(tx) > max_translation or abs(ty) > max_translation):
            return 1e6 + abs(tx) + abs(ty)
        distances = tree.query(_transform(source_fit, yaw, tx, ty), k=1)[0]
        return _trimmed_mean(distances, trim)

    optimized = minimize(objective, np.array([best_yaw, *best_translation]), method="Nelder-Mead",
                         options={"xatol": 0.005, "fatol": 1e-5, "maxiter": 240})
    transform = [float(value) for value in optimized.x]
    fit_score = float(objective(transform))
    exclusion = np.abs(candidates - best_yaw) >= 3.0
    second_score = float(np.min(scores[exclusion])) if bool(exclusion.any()) else None
    heldout = _forward_metrics(source_eval, reference_eval, transform)
    symmetric = _metrics(deterministic_sample(source, evaluation_points), reference_eval, transform)
    return dict(
        yaw_deg=transform[0], translation_xy_m=transform[1:],
        fit_score_m=fit_score,
        heldout=heldout,
        full_symmetric=symmetric,
        coarse_best_yaw_deg=best_yaw,
        second_peak_score_m=second_score,
        peak_margin_m=(second_score - float(scores[best_index]) if second_score is not None else None),
        fit_source_points=len(source_fit), fit_reference_points=len(reference_fit),
        evaluation_source_points=len(source_eval), evaluation_reference_points=len(reference_eval),
        optimizer_success=bool(optimized.success),
        selection="median_z+0.5m_to_4.5m",
    )


def validate_temporal_rigidity(segment_alignments, maximum_yaw_spread=1.0,
                               maximum_translation_spread=0.3):
    """Reject one-transform stitching when capture segments need different poses."""
    if len(segment_alignments) < 2:
        raise ValueError("至少需要两个分时段配准结果")
    yaws = np.array([item["yaw_deg"] for item in segment_alignments], dtype=float)
    translations = np.array([item["translation_xy_m"] for item in segment_alignments], dtype=float)
    yaw_spread = float(np.ptp(yaws))
    centre = np.median(translations, axis=0)
    translation_spread = float(np.max(np.linalg.norm(translations - centre, axis=1)))
    return dict(
        yaw_spread_deg=yaw_spread,
        translation_spread_m=translation_spread,
        maximum_yaw_spread_deg=maximum_yaw_spread,
        maximum_translation_spread_m=maximum_translation_spread,
        rigid=bool(yaw_spread <= maximum_yaw_spread
                   and translation_spread <= maximum_translation_spread),
    )


def angular_coverage_deg(point_sets):
    points = np.concatenate([np.asarray(points)[:, :2] for points in point_sets])
    angles = np.mod(np.degrees(np.arctan2(points[:, 1], points[:, 0])), 360.0)
    angles = np.sort(angles)
    if len(angles) < 2:
        return 0.0
    gaps = np.diff(np.concatenate((angles, angles[:1] + 360.0)))
    return float(360.0 - gaps.max())
