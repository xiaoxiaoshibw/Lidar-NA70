"""Stage: full-FOV wire proposals (W4, synthetic-first implementation).

Segments the batch's valid returns into lateral-slab connected components:
rays are bucketed by (azimuth slab, elevation slab); slabs are 4-connected
in (az, el) index space; each component meeting the lineal-extent budgets
becomes a `Proposal`. Deterministic output: components are ranked by
(-dominant_axis_span_m, first slab id) and truncated at max_components.

This proposal layer intentionally stays geometric and cheap. The docs
(NA70_WIRE_ALGORITHMS.md §5) leave Hough-lines / PCA linearity as *later*
contenders; any replacement must beat this baseline on the frozen SYNTHETIC +
reviewed sets first.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..observations import RayBatch
from .proposals import Proposal


@dataclass(frozen=True)
class FullFovConfig:
    azimuth_slab_deg: float = 2.0
    elevation_slab_deg: float = 2.0
    min_component_points: int = 6
    min_horizontal_extent_m: float = 8.0
    max_component_spatial_diameter_m: float = 120.0
    min_component_span_along_dominant_axis_m: float = 6.0
    max_components: int = 32


def validate_full_fov_config(cfg: FullFovConfig) -> FullFovConfig:
    if cfg.azimuth_slab_deg <= 0 or cfg.elevation_slab_deg <= 0:
        raise ValueError("slab sizes must be positive")
    if cfg.min_component_points < 2:
        raise ValueError("min_component_points must be >= 2")
    if cfg.min_horizontal_extent_m <= 0:
        raise ValueError("min_horizontal_extent_m must be positive")
    if cfg.max_component_spatial_diameter_m <= 0:
        raise ValueError("max_component_spatial_diameter_m must be positive")
    if cfg.min_component_span_along_dominant_axis_m <= 0:
        raise ValueError("min_component_span_along_dominant_axis_m must be positive")
    if cfg.max_components < 1:
        raise ValueError("max_components must be >= 1")
    return cfg


def _connected_components(labels: np.ndarray) -> list[np.ndarray]:
    """4-connected components over integer slab labels (N,2)."""
    if labels.size == 0:
        return []
    lookup: dict[tuple, list[int]] = {}
    for position, key in enumerate(labels):
        lookup.setdefault((int(key[0]), int(key[1])), []).append(position)
    visited = np.zeros(len(labels), dtype=bool)
    components: list[np.ndarray] = []
    for start in range(len(labels)):
        if visited[start]:
            continue
        stack = [start]
        visited[start] = True
        members: list[int] = []
        while stack:
            cursor = stack.pop()
            members.append(cursor)
            a, e = int(labels[cursor, 0]), int(labels[cursor, 1])
            for delta in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for idx in lookup.get((a + delta[0], e + delta[1]), ()):  # noqa: B905
                    if not visited[idx]:
                        visited[idx] = True
                        stack.append(idx)
        components.append(np.asarray(members, dtype=np.int64))
    return components


def propose_full_fov(batch: RayBatch, cfg: FullFovConfig) -> tuple:
    cfg = validate_full_fov_config(cfg)
    valid = batch.valid_return & batch.direction_known
    if not valid.any():
        return tuple()
    xyz = batch.points[valid, :3]
    if xyz.shape[0] < cfg.min_component_points:
        return tuple()

    ranges_xy = np.linalg.norm(xyz[:, :2], axis=1)
    azimuth_deg = np.degrees(np.arctan2(xyz[:, 1], np.maximum(np.abs(xyz[:, 0]), 1e-6)))
    elevation_deg = np.degrees(np.arctan2(xyz[:, 2], np.maximum(ranges_xy, 1e-6)))
    labels = np.column_stack([
        np.floor(azimuth_deg / cfg.azimuth_slab_deg).astype(np.int64),
        np.floor(elevation_deg / cfg.elevation_slab_deg).astype(np.int64),
    ])
    raw_components = _connected_components(labels)
    batch_positions = np.flatnonzero(valid)

    enriched: list[tuple] = []
    for members in raw_components:
        if len(members) < cfg.min_component_points:
            continue
        pts = xyz[members]
        horizontal_min = pts[:, :2].min(axis=0)
        horizontal_max = pts[:, :2].max(axis=0)
        horizontal_extent = float(np.linalg.norm(horizontal_max - horizontal_min))
        if horizontal_extent < cfg.min_horizontal_extent_m:
            continue
        spatial_min = pts.min(axis=0)
        spatial_max = pts.max(axis=0)
        spatial_diameter = float(np.linalg.norm(spatial_max - spatial_min))
        if spatial_diameter > cfg.max_component_spatial_diameter_m:
            continue
        centred = pts - pts.mean(axis=0)
        _, _, vh = np.linalg.svd(centred, full_matrices=False)
        dominant = vh[0]
        projections = centred @ dominant
        span = float(projections.max() - projections.min())
        if span < cfg.min_component_span_along_dominant_axis_m:
            continue
        aabb = np.array([spatial_min[0], spatial_max[0],
                         spatial_min[1], spatial_max[1],
                         spatial_min[2], spatial_max[2]])
        enriched.append((members, pts, aabb, span))

    enriched.sort(key=lambda item: (-item[3], item[2].tolist()))
    proposals = []
    for members, _pts, aabb, _span in enriched[: cfg.max_components]:
        member_mask = np.zeros(batch.ray_count, dtype=bool)
        member_mask[batch_positions[members]] = True
        proposals.append(Proposal(kind="full_fov_component", aabb_m=aabb,
                                  mask=member_mask))
    return tuple(proposals)
