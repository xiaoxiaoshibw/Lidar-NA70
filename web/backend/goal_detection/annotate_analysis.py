"""Brush-region goal geometry analysis for the annotation workbench.

The labeler paints the goal region freehand on the locked XY top-down view;
this module turns the painted strokes plus the RAW accumulated window cloud
into a measured goal-frame proposal.  It is a measurement aid, not a detector:
it never gates anything, never feeds the tracking pipeline, and every output
carries the point support it was computed from so the labeler can judge it.
Known-sensor lessons are honored: thin-post returns may start above the ground,
so the base height is measured, never assumed to be zero.
"""
from __future__ import annotations

import math

import numpy as np


def _stroke_mask(xy: np.ndarray, strokes: list, brush_radius: float) -> np.ndarray:
    """Points whose XY distance to any stroke segment is within the brush."""
    keep = np.zeros(len(xy), dtype=bool)
    radius_sq = float(brush_radius) ** 2
    for stroke in strokes:
        polyline = np.asarray(stroke, dtype=np.float64)
        if len(polyline) == 1:
            delta = xy - polyline[0]
            keep |= (delta * delta).sum(axis=1) <= radius_sq
            continue
        for a, b in zip(polyline[:-1], polyline[1:]):
            segment = b - a
            length_sq = float(segment @ segment)
            if length_sq <= 1e-12:
                delta = xy - a
                keep |= (delta * delta).sum(axis=1) <= radius_sq
                continue
            # Parameter of the projection clamped to the segment; the brush is
            # a capsule (discs swept along the polyline), not an infinite band.
            t = np.clip(((xy - a) @ segment) / length_sq, 0.0, 1.0)
            closest = a + t[:, None] * segment
            delta = xy - closest
            keep |= (delta * delta).sum(axis=1) <= radius_sq
    return keep


def _densest_bin_center(values: np.ndarray, lo: float, hi: float,
                        bin_size: float) -> tuple[float, int] | None:
    """Median of the densest bin inside [lo, hi]; None when the range is empty."""
    window = values[(values >= lo) & (values <= hi)]
    if len(window) == 0:
        return None
    bins = max(int(math.ceil((hi - lo) / bin_size)), 1)
    histogram, edges = np.histogram(window, bins=bins, range=(lo, hi))
    peak = int(np.argmax(histogram))
    selected = window[(window >= edges[peak]) & (window <= edges[peak + 1])]
    if len(selected) == 0:
        return None
    return float(np.median(selected)), int(len(selected))


def _column_stats(points: np.ndarray, t: np.ndarray, s: np.ndarray,
                  t_center: float, s_center: float, radius: float,
                  base_quantile: float, top_quantile: float) -> dict | None:
    """Measured support of one post column: extent, base/top height, count."""
    near = (np.abs(t - t_center) <= radius) & (np.abs(s - s_center) <= radius)
    if int(near.sum()) == 0:
        return None
    z = points[near, 2]
    return {
        "points": int(near.sum()),
        "xy": [round(float(points[near, 0].mean()), 3),
               round(float(points[near, 1].mean()), 3)],
        "base_z": round(float(np.quantile(z, base_quantile)), 3),
        "top_z": round(float(np.quantile(z, top_quantile)), 3),
    }


def _split_clusters(region: np.ndarray, cfg: dict) -> list[np.ndarray]:
    """Split the painted region into separated dense blobs (grid union-find).

    Only cells holding at least ``cluster_min_cell_points`` points count as
    structure: sparse ground/background returns between two goals must not
    chain them into one cluster.  Used when a single stroke group spans
    several goals — each blob is then measured as its own hypothesis.
    """
    cell = float(cfg["cluster_cell_size"])
    link = int(cfg["cluster_link_cells"])
    min_cell_points = int(cfg["cluster_min_cell_points"])
    keys = np.floor(region[:, :2] / cell).astype(np.int64)
    unique, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    dense_cells = unique[counts >= min_cell_points]
    dense_mask = counts[inverse] >= min_cell_points
    if len(dense_cells) < 2:
        return [region] if len(region) >= int(cfg["min_cluster_points"]) else []
    dense_points = region[dense_mask]
    cell_index = {tuple(cell): i for i, cell in enumerate(map(tuple, dense_cells))}
    cell_points: dict[int, list[int]] = {}
    for row, key in enumerate(map(tuple, keys[dense_mask])):
        cell_points.setdefault(cell_index[key], []).append(row)
    parent = list(range(len(dense_points)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    offsets = [(dx, dy) for dx in range(-link, link + 1) for dy in range(-link, link + 1)]
    for cell_id, rows in cell_points.items():
        base = dense_cells[cell_id]
        for neighbor in rows[1:]:
            union(rows[0], neighbor)
        for dx, dy in offsets:
            other = cell_index.get((int(base[0]) + dx, int(base[1]) + dy))
            if other is not None:
                union(rows[0], cell_points[other][0])
    groups: dict[int, list[int]] = {}
    for row in range(len(dense_points)):
        groups.setdefault(find(row), []).append(row)
    clusters = [dense_points[np.asarray(rows)] for rows in groups.values()
                if len(rows) >= int(cfg["min_cluster_points"])]
    clusters.sort(key=len, reverse=True)
    return clusters[:int(cfg["max_clusters"])]


def _measure_single_region(region: np.ndarray, brush_radius: float,
                           cfg: dict, detection_cfg: dict) -> dict:
    """Measure one goal hypothesis from an already-selected region."""
    center = region[:, :2].mean(axis=0)
    centered = region[:, :2] - center
    covariance = centered.T @ centered / max(len(centered) - 1, 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    axis = eigenvectors[:, int(np.argmax(eigenvalues))]
    perp = np.array([-axis[1], axis[0]])
    t = centered @ axis
    s = centered @ perp

    trim = float(cfg["trim_percentile"])
    lo = float(np.quantile(t, trim / 100.0))
    hi = float(np.quantile(t, 1.0 - trim / 100.0))
    span = hi - lo
    bin_size = float(cfg["post_bin_size"])
    band = span * float(cfg["end_band_fraction"])
    warnings: list[str] = []
    left = _densest_bin_center(t, lo, lo + band, bin_size)
    right = _densest_bin_center(t, hi - band, hi, bin_size)
    min_post = int(cfg["min_post_points"])
    if left is None or right is None or right[0] - left[0] < float(cfg["min_width"]):
        return {"status": "degenerate_span", "region_points": int(len(region)),
                "axis_span_m": round(span, 3),
                "warnings": ["沿主轴方向未找到左右两个分离的密集立柱；"
                             "请重新圈选，让两根立柱和横梁都在画笔覆盖内"]}
    if left[1] < min_post or right[1] < min_post:
        # 两端密集度不对称：一端是立柱级的支撑，另一端只有网线/横梁的稀疏回波。
        return {"status": "degenerate_span", "region_points": int(len(region)),
                "axis_span_m": round(span, 3),
                "end_support": [int(left[1]), int(right[1])],
                "warnings": [f"轴向两端密集度不对称（{left[1]} 对 {right[1]} 点），"
                             "疑似只圈中了一根立柱；请把两根立柱都画进区域内"]}

    column_radius = float(cfg["post_column_radius"])
    left_near = np.abs(t - left[0]) <= column_radius
    right_near = np.abs(t - right[0]) <= column_radius
    left_s = float(np.median(s[left_near])) if int(left_near.sum()) else 0.0
    right_s = float(np.median(s[right_near])) if int(right_near.sum()) else 0.0
    left_stats = _column_stats(region, t, s, left[0], left_s, column_radius,
                               float(cfg["base_z_quantile"]), float(cfg["top_z_quantile"]))
    right_stats = _column_stats(region, t, s, right[0], right_s, column_radius,
                                float(cfg["base_z_quantile"]), float(cfg["top_z_quantile"]))
    if left_stats is None or right_stats is None:
        return {"status": "degenerate_span", "region_points": int(len(region)),
                "axis_span_m": round(span, 3),
                "warnings": ["立柱位置缺少支撑点，无法测量高度"]}

    # Thickness: lateral spread of each post column, measured not assumed.
    def column_thickness(t_center_value: float, s_center_value: float) -> float:
        near = (np.abs(t - t_center_value) <= column_radius) \
            & (np.abs(s - s_center_value) <= column_radius)
        if int(near.sum()) < 2:
            return float(cfg["min_thickness"])
        spread = float(np.quantile(s[near], 0.95) - np.quantile(s[near], 0.05))
        return min(max(spread, float(cfg["min_thickness"])), float(cfg["max_thickness"]))

    left_s = float(np.median(s[np.abs(t - left[0]) <= column_radius])) \
        if int((np.abs(t - left[0]) <= column_radius).sum()) else 0.0
    right_s = float(np.median(s[np.abs(t - right[0]) <= column_radius])) \
        if int((np.abs(t - right[0]) <= column_radius).sum()) else 0.0
    thickness = max(column_thickness(left[0], left_s), column_thickness(right[0], right_s))

    left_xy = center + axis * left[0] + perp * left_s
    right_xy = center + axis * right[0] + perp * right_s
    width = float(np.linalg.norm(right_xy - left_xy))
    base_z = min(left_stats["base_z"], right_stats["base_z"])
    top_z = max(left_stats["top_z"], right_stats["top_z"])
    height = max(top_z - base_z, float(cfg["min_thickness"]))
    yaw = math.atan2(float(right_xy[1] - left_xy[1]), float(right_xy[0] - left_xy[0]))
    box_center = [round(float((left_xy[0] + right_xy[0]) / 2), 3),
                  round(float((left_xy[1] + right_xy[1]) / 2), 3),
                  round(float((base_z + top_z) / 2), 3)]

    width_range = detection_cfg["frame"]["width"]
    height_range = detection_cfg["pole"]["height"]
    checks = {
        "width_in_expected_range": bool(width_range[0] <= width <= width_range[1]),
        "height_in_expected_range": bool(height_range[0] <= height <= height_range[1]),
    }
    if not checks["width_in_expected_range"]:
        warnings.append(f"测量宽度 {width:.2f} m 在常规门宽 {width_range[0]}–{width_range[1]} m 之外；"
                        "若画笔未完整覆盖两根立柱或带入了背景，请修正圈选后重新分析")
    if not checks["height_in_expected_range"]:
        warnings.append(f"测量高度 {height:.2f} m 在常规柱高 {height_range[0]}–{height_range[1]} m 之外；"
                        "薄立柱下部回波可能缺失，请核对顶部/底部支撑点")

    return {
        "status": "ok",
        "region_points": int(len(region)),
        "brush_radius": round(float(brush_radius), 3),
        "axis_span_m": round(span, 3),
        "left_post": {**left_stats, "axis_support": int(left[1])},
        "right_post": {**right_stats, "axis_support": int(right[1])},
        "thickness_m": round(thickness, 3),
        "base_z": round(base_z, 3),
        "top_z": round(top_z, 3),
        "box": {
            "center": box_center,
            "size": [round(thickness, 3), round(width, 3), round(height, 3)],
            "yaw": round(yaw, 4),
        },
        "checks": checks,
        "warnings": warnings,
    }


def analyze_brush_region(points: np.ndarray, strokes: list, brush_radius: float,
                         cfg: dict, detection_cfg: dict) -> dict:
    """Measure a goal-frame proposal inside the painted region.

    ``points`` is an (N, >=3) array of finite sensor-frame coordinates of the
    raw accumulated window; ``strokes`` are polylines in world XY.  Returns a
    proposal dict with ``status`` plus per-post measurements, the contract box
    and explicit checks — the caller displays everything and the labeler
    decides; nothing here is an acceptance gate.

    If the measured width is absurdly large, the region probably spans
    several goals painted as one stroke group; the region is then split into
    spatially separated blobs and each blob gets its own proposal
    (``status: "ok_multiple"``, ``proposals: [...]``).
    """
    min_points = int(cfg["min_region_points"])
    if len(points) == 0 or not strokes:
        return {"status": "insufficient_points", "region_points": 0,
                "warnings": ["区域内没有点：先画笔画出球门区域"]}
    xy = np.asarray(points[:, :2], dtype=np.float64)
    mask = _stroke_mask(xy, strokes, float(brush_radius))
    region = points[mask]
    if len(region) < min_points:
        return {"status": "insufficient_points", "region_points": int(len(region)),
                "min_region_points": min_points,
                "warnings": [f"画笔区域内仅 {len(region)} 个点（至少 {min_points}）,"
                             "请沿门柱和横梁多画几笔或增大画笔半径"]}

    single = _measure_single_region(region, brush_radius, cfg, detection_cfg)
    width = (single.get("box") or {}).get("size", [0, 0])[1]
    if single["status"] != "ok" or width <= float(cfg["multi_split_width_threshold"]):
        return single
    # 提案宽度异常：大概率把多个分离目标圈成了一组。拆开后各自测量。
    clusters = _split_clusters(region, cfg)
    too_wide_hint = ("提案宽度异常偏大；若同一组画笔圈了多个球门，"
                     "请画完一个先入列（回车/G）再画下一个")
    if len(clusters) < 2:
        single["warnings"].append(too_wide_hint)
        return single
    proposals = [measured for cluster in clusters
                 if (measured := _measure_single_region(cluster, brush_radius, cfg,
                                                        detection_cfg))["status"] == "ok"]
    if len(proposals) < 2:
        single["warnings"].append(too_wide_hint)
        return single
    return {
        "status": "ok_multiple",
        "region_points": int(len(region)),
        "brush_radius": round(float(brush_radius), 3),
        "cluster_count": len(clusters),
        "proposals": proposals,
        "warnings": [f"画笔覆盖了 {len(clusters)} 个分离区域，已拆分为 {len(proposals)} 个独立球门提案，"
                     "已分别入列，请逐组核对"],
    }
