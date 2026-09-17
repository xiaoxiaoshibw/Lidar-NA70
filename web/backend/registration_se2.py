"""Localizability-aware, Z-up SE(2) point-cloud registration candidates.

The implementation is deliberately NumPy/SciPy-only so it can be regression
tested on Windows before selecting a Jetson backend.  Weak eigendirections are
projected out (TSVD) rather than converted into confident motion.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

from .mapping import estimate_normals, invert, rotation_deg, translation_m

SE2_REGISTRATION_VERSION = "na70-localizable-se2-v1"
DOF_NAMES = ("x", "y", "z", "roll", "pitch", "yaw")


def _local_geometry(points, neighbors=12):
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        return np.zeros((len(points), 3)), np.zeros((len(points), 3))
    k = max(3, min(int(neighbors), len(points)))
    indices = cKDTree(points).query(points, k=k)[1]
    neighbourhoods = points[indices]
    centred = neighbourhoods - neighbourhoods.mean(axis=1, keepdims=True)
    covariance = np.einsum("nki,nkj->nij", centred, centred) / k
    values, vectors = np.linalg.eigh(covariance)
    return values, vectors


def scene_structure_statistics(points, neighbors=12):
    """Geometry-only evidence; labels are candidates, never semantic truth."""
    values, vectors = _local_geometry(points, neighbors)
    if not len(values):
        return dict(point_count=0, ground_like=0, vertical_like=0,
                    thin_structure_like=0, irregular_or_unstable_like=0)
    normals = vectors[:, :, 0]
    scale = np.maximum(values[:, 2], 1e-12)
    linearity = (values[:, 2] - values[:, 1]) / scale
    roughness = values[:, 0] / np.maximum(values.sum(axis=1), 1e-12)
    ground = np.abs(normals[:, 2]) >= 0.85
    vertical = np.abs(normals[:, 2]) <= 0.30
    thin = linearity >= 0.60
    irregular = roughness >= 0.12
    return dict(
        point_count=len(values),
        ground_like=int(ground.sum()),
        vertical_like=int(vertical.sum()),
        thin_structure_like=int(thin.sum()),
        irregular_or_unstable_like=int(irregular.sum()),
        semantics="geometry_candidates_only",
    )


def select_se2_structures(points, neighbors=12, normal_z_max=0.75):
    """Select surfaces whose normals can directly constrain x/y/yaw.

    A horizontal ground plane has a Z-aligned normal and contributes zero rows
    to the SE(2) point-to-plane Jacobian.  Excluding it also stops its density
    from dominating GICP/NDT correspondences and quality metrics.
    """
    points = np.asarray(points, dtype=np.float64)
    values, vectors = _local_geometry(points, neighbors)
    if not len(values):
        return points, np.zeros(0, dtype=bool)
    normals = vectors[:, :, 0]
    mask = np.abs(normals[:, 2]) <= float(normal_z_max)
    return points[mask], mask


def _observability(normal_matrix, names, ratio):
    matrix = np.asarray(normal_matrix, dtype=np.float64)
    values, vectors = np.linalg.eigh((matrix + matrix.T) * 0.5)
    values = np.maximum(values, 0.0)
    largest = float(values[-1]) if len(values) else 0.0
    normalized = values / largest if largest > 0 else np.zeros_like(values)
    observable = normalized >= ratio
    weighted = vectors @ np.diag(normalized) @ vectors.T if largest > 0 else np.zeros_like(matrix)
    return dict(
        dof_scores={name: round(float(weighted[i, i]), 8) for i, name in enumerate(names)},
        eigenvalues=[float(value) for value in values],
        normalized_eigenvalues=[float(value) for value in normalized],
        eigenvectors=vectors.tolist(),
        observable_eigendirections=observable.tolist(),
        rank=int(observable.sum()),
        condition_number=(float(largest / values[observable][0])
                          if bool(observable.any()) and values[observable][0] > 0 else None),
    )


def point_to_plane_observability(points, normals, ratio=0.01):
    points = np.asarray(points, dtype=np.float64)
    normals = np.asarray(normals, dtype=np.float64)
    if len(points) != len(normals):
        raise ValueError("points 与 normals 数量不一致")
    if len(points) == 0:
        report = _observability(np.zeros((6, 6)), DOF_NAMES, ratio)
        report["rotation_scale_m"] = None
        return report
    extent = max(1e-3, float(np.median(np.linalg.norm(points - points.mean(axis=0), axis=1))))
    jacobian = np.hstack((normals, np.cross(points, normals) / extent))
    report = _observability(jacobian.T @ jacobian, DOF_NAMES, ratio)
    report["rotation_scale_m"] = extent
    return report


def _se2_delta(update):
    yaw, tx, ty = map(float, update)
    cosine, sine = math.cos(yaw), math.sin(yaw)
    result = np.eye(4)
    result[:2, :2] = [[cosine, -sine], [sine, cosine]]
    result[0, 3], result[1, 3] = tx, ty
    return result


def _huber_weights(errors, delta):
    errors = np.abs(np.asarray(errors, dtype=np.float64))
    return np.where(errors <= delta, 1.0, delta / np.maximum(errors, 1e-12))


def _projected_solve(matrix, gradient, ratio):
    values, vectors = np.linalg.eigh((matrix + matrix.T) * 0.5)
    largest = float(max(0.0, values[-1])) if len(values) else 0.0
    keep = values >= largest * ratio if largest > 0 else np.zeros(len(values), dtype=bool)
    if not bool(keep.any()):
        return np.zeros(len(gradient)), _observability(matrix, ("yaw", "x", "y"), ratio)
    coordinates = vectors[:, keep].T @ gradient
    update = -(vectors[:, keep] @ (coordinates / values[keep]))
    return update, _observability(matrix, ("yaw", "x", "y"), ratio)


def _point_to_plane_system(moved, matched, normals, cfg):
    residual = np.einsum("ij,ij->i", moved - matched, normals)
    jacobian = np.column_stack((-normals[:, 0] * moved[:, 1]
                                + normals[:, 1] * moved[:, 0],
                                normals[:, 0], normals[:, 1]))
    weights = _huber_weights(residual, cfg.get("se2_huber_delta", 0.20))
    return jacobian.T @ (weights[:, None] * jacobian), jacobian.T @ (weights * residual)


def _covariances(points, neighbors):
    values, vectors = _local_geometry(points, neighbors)
    if not len(values):
        return np.empty((0, 3, 3))
    # GICP plane model: retain small but non-zero variance along the normal.
    scale = np.maximum(values[:, 2], 1e-4)
    shaped = np.column_stack((np.maximum(scale * 1e-3, 1e-4), scale, scale))
    return np.einsum("nij,nj,nkj->nik", vectors, shaped, vectors)


def _voxel_centroids(points, resolution):
    points = np.asarray(points, dtype=np.float64)
    if not len(points):
        return points
    keys = np.floor(points / float(resolution)).astype(np.int64)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    counts = np.bincount(inverse).astype(np.float64)
    result = np.empty((len(counts), 3), dtype=np.float64)
    for axis in range(3):
        result[:, axis] = np.bincount(inverse, weights=points[:, axis]) / counts
    return result


def _gicp_system(moved, matched, source_cov, target_cov, rotation, cfg):
    count = len(moved)
    rotated = np.einsum("ij,njk,lk->nil", rotation, source_cov, rotation)
    covariance = rotated + target_cov + np.eye(3)[None] * cfg.get("gicp_regularization", 1e-3)
    information = np.linalg.inv(covariance)
    residual = moved - matched
    jacobian = np.zeros((count, 3, 3))
    jacobian[:, :, 0] = np.column_stack((-moved[:, 1], moved[:, 0], np.zeros(count)))
    jacobian[:, 0, 1] = 1.0
    jacobian[:, 1, 2] = 1.0
    mahalanobis = np.sqrt(np.maximum(0.0, np.einsum("ni,nij,nj->n", residual, information, residual)))
    weights = _huber_weights(mahalanobis, cfg.get("gicp_huber_delta", 1.5))
    matrix = np.einsum("nai,nab,nbj,n->ij", jacobian, information, jacobian, weights)
    gradient = np.einsum("nai,nab,nb,n->i", jacobian, information, residual, weights)
    return matrix, gradient


def _ndt_cells(points, resolution, min_points, regularization):
    keys = np.floor(np.asarray(points) / resolution).astype(np.int64)
    order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
    keys, points = keys[order], np.asarray(points)[order]
    unique, starts, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
    cells = {}
    for key, start, count in zip(unique, starts, counts):
        if count < min_points:
            continue
        group = points[start:start + count]
        covariance = np.cov(group.T) + np.eye(3) * regularization
        cells[tuple(key)] = (group.mean(axis=0), np.linalg.inv(covariance))
    return cells


def _ndt_system(moved, cells, resolution, cfg):
    selected_points, means, information = [], [], []
    for point in moved:
        cell = cells.get(tuple(np.floor(point / resolution).astype(np.int64)))
        if cell is not None:
            selected_points.append(point)
            means.append(cell[0])
            information.append(cell[1])
    if not selected_points:
        return None, None, 0
    moved = np.asarray(selected_points)
    means = np.asarray(means)
    information = np.asarray(information)
    residual = moved - means
    count = len(moved)
    jacobian = np.zeros((count, 3, 3))
    jacobian[:, :, 0] = np.column_stack((-moved[:, 1], moved[:, 0], np.zeros(count)))
    jacobian[:, 0, 1] = 1.0
    jacobian[:, 1, 2] = 1.0
    mahalanobis = np.sqrt(np.maximum(0.0, np.einsum("ni,nij,nj->n", residual, information, residual)))
    weights = _huber_weights(mahalanobis, cfg.get("ndt_huber_delta", 2.0))
    matrix = np.einsum("nai,nab,nbj,n->ij", jacobian, information, jacobian, weights)
    gradient = np.einsum("nai,nab,nb,n->i", jacobian, information, residual, weights)
    return matrix, gradient, count


def register_se2(source, target, initial, cfg, method="point_to_plane"):
    """Register while freezing z/roll/pitch and projecting weak SE(2) updates."""
    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    minimum = int(cfg.get("min_points", 60))
    quality = dict(state=None, reason=None, method=f"se2_{method}", point_count=len(source),
                   overlap=0.0, rmse=None, iterations=0, observability=None)
    if len(source) < minimum or len(target) < minimum:
        quality.update(state="insufficient_points", reason="too_few_points")
        return None, quality
    if method not in ("point_to_plane", "gicp", "ndt"):
        raise ValueError("未知 SE(2) 配准方法")

    source_all, target_all = source, target
    source, source_structure_mask = select_se2_structures(
        source_all, cfg.get("normal_neighbors", 10),
        cfg.get("se2_structure_normal_z_max", 0.75))
    target, target_structure_mask = select_se2_structures(
        target_all, cfg.get("normal_neighbors", 10),
        cfg.get("se2_structure_normal_z_max", 0.75))
    quality.update(
        source_structure_points=int(source_structure_mask.sum()),
        target_structure_points=int(target_structure_mask.sum()),
        structure_selection="abs(normal_z)<=se2_structure_normal_z_max",
    )
    if len(source) < minimum or len(target) < minimum:
        quality.update(
            state="degenerate_geometry", reason="se2_structure_insufficient",
            frozen_directions=["yaw", "x", "y"],
            observability=_observability(np.zeros((3, 3)), ("yaw", "x", "y"),
                                         cfg.get("se2_observability_ratio", 0.01)),
        )
        return None, quality

    pose = np.asarray(initial, dtype=np.float64).copy()
    final_tree = cKDTree(target)
    if method == "gicp":
        levels = cfg.get("gicp_resolutions", [0.8, 0.4, 0.25])
    elif method == "ndt":
        levels = cfg.get("ndt_resolutions", [2.0, 1.0])
    else:
        levels = [None]
    quality["resolutions_m"] = [float(value) for value in levels if value is not None]
    final_observability = None
    iterations = 0
    for resolution in levels:
        if method == "gicp":
            stage_source = _voxel_centroids(source, float(resolution))
            stage_target = _voxel_centroids(target, float(resolution))
        else:
            stage_source, stage_target = source, target
        tree = cKDTree(stage_target)
        target_normals = (estimate_normals(stage_target, cfg.get("normal_neighbors", 10))
                          if method == "point_to_plane" else None)
        source_cov = (_covariances(stage_source, cfg.get("gicp_neighbors", 12))
                      if method == "gicp" else None)
        target_cov = (_covariances(stage_target, cfg.get("gicp_neighbors", 12))
                      if method == "gicp" else None)
        cells = (_ndt_cells(stage_target, float(resolution), cfg.get("ndt_min_points", 5),
                            cfg.get("ndt_regularization", 0.05)) if method == "ndt" else None)
        for _ in range(int(cfg.get("max_iterations", 30))):
            iterations += 1
            moved = stage_source @ pose[:3, :3].T + pose[:3, 3]
            distances, indices = tree.query(moved, k=1)
            valid = distances < cfg.get("correspondence_distance", 0.6)
            if int(valid.sum()) < minimum:
                quality.update(state="insufficient_points", reason="no_correspondence", iterations=iterations)
                return None, quality
            cutoff = np.quantile(distances[valid], cfg.get("correspondence_trim", 0.85))
            valid &= distances <= cutoff
            moved_v, matched = moved[valid], stage_target[indices[valid]]
            if method == "point_to_plane":
                matrix, gradient = _point_to_plane_system(
                    moved_v, matched, target_normals[indices[valid]], cfg)
            elif method == "gicp":
                matrix, gradient = _gicp_system(
                    moved_v, matched, source_cov[valid], target_cov[indices[valid]], pose[:3, :3], cfg)
            else:
                matrix, gradient, count = _ndt_system(moved, cells, float(resolution), cfg)
                if count < minimum:
                    quality.update(state="insufficient_points", reason="ndt_cells_sparse", iterations=iterations)
                    return None, quality
            update, final_observability = _projected_solve(
                matrix, gradient, cfg.get("se2_observability_ratio", 0.01))
            update[0] = np.clip(update[0], -math.radians(cfg.get("max_iteration_rotation_deg", 5.0)),
                                math.radians(cfg.get("max_iteration_rotation_deg", 5.0)))
            update[1:] = np.clip(update[1:], -cfg.get("max_iteration_translation", 0.5),
                                 cfg.get("max_iteration_translation", 0.5))
            pose = _se2_delta(update) @ pose
            if np.linalg.norm(update) < cfg.get("convergence_m", 1e-4):
                break

    moved = source @ pose[:3, :3].T + pose[:3, 3]
    distances = final_tree.query(moved, k=1)[0]
    valid = distances < cfg.get("correspondence_distance", 0.6)
    overlap = float(valid.mean())
    rmse = float(np.sqrt(np.mean(distances[valid] ** 2))) if bool(valid.any()) else None
    delta = invert(np.asarray(initial)) @ pose
    quality.update(
        iterations=iterations, overlap=round(overlap, 6), rmse=rmse,
        translation_step_m=translation_m(delta), rotation_step_deg=rotation_deg(delta),
        observability=final_observability,
        frozen_directions=([name for name, score in (final_observability or {}).get("dof_scores", {}).items()
                            if score < cfg.get("se2_observability_ratio", 0.01)]),
        candidate_pose=pose.tolist(),
    )
    if final_observability is None or final_observability["rank"] == 0:
        quality.update(state="degenerate_geometry", reason="se2_unobservable")
    elif overlap < cfg.get("min_overlap", 0.55):
        quality.update(state="quality_failed", reason="overlap_low")
    elif rmse is None or rmse > cfg.get("max_rmse", 0.18):
        quality.update(state="quality_failed", reason="rmse_high")
    elif translation_m(delta) > cfg.get("max_translation_step", 0.5):
        quality.update(state="quality_failed", reason="translation_jump")
    elif rotation_deg(delta) > cfg.get("max_rotation_step", 15.0):
        quality.update(state="quality_failed", reason="rotation_jump")
    else:
        quality.update(state="accepted", reason="accepted")
        return pose, quality
    return None, quality
