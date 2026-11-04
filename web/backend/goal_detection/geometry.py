"""Geometric primitives for goal-frame description and scoring.

Pole extraction works on a horizontal cell grid rather than global clusters:
a goal frame is a connected structure (posts, crossbar, net), so single-link
clustering merges everything into one blob.  A cell that spans enough height
in z while staying narrow in x, y is a *column cell*; adjacent column cells
are merged into pole candidates.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class VerticalPole:
    """A thin, tall point column that can serve as a goalpost candidate."""

    center_xy: np.ndarray      # (2,) mean of the column in x, y
    base_z: float              # lowest point of the column
    top_z: float               # highest point of the column
    radius: float              # horizontal RMS spread around the column axis
    count: int
    # Indexes into the xyz array passed to ``find_poles``.  Keeping provenance
    # here lets the debug API show the exact samples that created a candidate
    # instead of reverse-engineering them from the final box.
    indices: np.ndarray | None = None

    @property
    def height(self) -> float:
        return self.top_z - self.base_z


def find_poles(xyz: np.ndarray, pole_cfg: dict, cell_size: float,
               diagnostics: dict | None = None,
               grid_offset: tuple[float, float] = (0.0, 0.0)) -> list[VerticalPole]:
    """Grid the points in x, y and merge cells that look like upright columns.

    A cell qualifies when it holds at least ``min_points`` points spanning at
    least ``height[0]`` in z with horizontal RMS spread below ``max_radius``.
    To reject walls and fences (where *every* cell is tall), a cell must also
    be laterally isolated: at least two of its four orthogonal neighbours are
    not tall columns.  A freestanding post has four free sides, a post in
    front of a wall keeps at least the two lateral ones, a wall edge keeps
    only one and wall interiors none.  Adjacent qualifying cells (8-
    neighbourhood) are then merged into one pole.
    """
    min_height = float(pole_cfg["height"][0])
    max_height = float(pole_cfg["height"][1])
    max_radius = float(pole_cfg["max_radius"])
    min_points = int(pole_cfg["min_points"])
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update({"input_points": int(len(xyz)), "cell_count": 0,
                            "tall_cells": 0, "isolated_cells": 0,
                            "output_poles": 0, "reason_counts": {}})
    reasons = diagnostics["reason_counts"] if diagnostics is not None else {}
    if len(xyz) == 0:
        return []
    offset = np.asarray(grid_offset, dtype=np.float64).reshape(1, 2)
    keys = np.floor((xyz[:, :2] + offset) / cell_size).astype(np.int64)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    sorted_keys = keys[order]
    boundaries = np.flatnonzero(np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1)) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [len(order)]))
    if diagnostics is not None:
        diagnostics["cell_count"] = len(starts)
    tall: dict[tuple[int, int], bool] = {}
    cell_indices: dict[tuple[int, int], np.ndarray] = {}
    for start, end in zip(starts, ends):
        idx = order[start:end]
        if len(idx) < min_points:
            reasons["insufficient_cell_points"] = reasons.get("insufficient_cell_points", 0) + 1
            continue
        block = xyz[idx]
        center_xy = block[:, :2].mean(axis=0)
        spread = np.linalg.norm(block[:, :2] - center_xy, axis=1)
        radius = float(np.sqrt((spread ** 2).mean()))
        if radius > max_radius:
            reasons["radius_too_large"] = reasons.get("radius_too_large", 0) + 1
            continue
        height = float(block[:, 2].max() - block[:, 2].min())
        if height < min_height:
            reasons["height_too_short"] = reasons.get("height_too_short", 0) + 1
            continue
        if height > max_height and radius > max_radius * 0.5:
            reasons["height_too_large_and_wide"] = reasons.get(
                "height_too_large_and_wide", 0) + 1
            continue
        key = tuple(int(v) for v in sorted_keys[start])
        tall[key] = True
        cell_indices[key] = idx
    if not tall:
        return []
    if diagnostics is not None:
        diagnostics["tall_cells"] = len(tall)
    # Keep only laterally isolated tall cells (walls have tall neighbours on
    # both sides of both axes; a post has free sides somewhere).
    candidates = {}
    for (cx, cy), idx in cell_indices.items():
        free = 0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (cx + dx, cy + dy) not in tall:
                free += 1
        if free >= 2:
            candidates[(cx, cy)] = idx
        else:
            reasons["not_laterally_isolated"] = reasons.get("not_laterally_isolated", 0) + 1
    if not candidates:
        return []
    if diagnostics is not None:
        diagnostics["isolated_cells"] = len(candidates)
    # Merge 8-adjacent qualifying cells (union-find over cell keys).
    parent = {key: key for key in candidates}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for cx, cy in candidates:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                other = (cx + dx, cy + dy)
                if other != (cx, cy) and other in candidates:
                    ra, rb = find((cx, cy)), find(other)
                    if ra != rb:
                        parent[rb] = ra
    groups: dict[tuple, list[np.ndarray]] = {}
    for key, idx in candidates.items():
        groups.setdefault(find(key), []).append(idx)
    poles = []
    for merged in groups.values():
        idx = np.concatenate(merged)
        block = xyz[idx]
        center_xy = block[:, :2].mean(axis=0)
        spread = np.linalg.norm(block[:, :2] - center_xy, axis=1)
        radius = float(np.sqrt((spread ** 2).mean()))
        poles.append(VerticalPole(center_xy=center_xy,
                                  base_z=float(block[:, 2].min()),
                                  top_z=float(block[:, 2].max()),
                                  radius=radius, count=len(block), indices=idx))
    poles.sort(key=lambda p: p.count, reverse=True)
    if diagnostics is not None:
        diagnostics["output_poles"] = len(poles)
    return poles


def pair_poles(poles: list[VerticalPole], pole_cfg: dict, frame_cfg: dict) -> list[tuple[VerticalPole, VerticalPole]]:
    """Match pole pairs by height similarity, base alignment and goal width."""
    width_lo, width_hi = (float(v) for v in frame_cfg["width"])
    height_tol = float(pole_cfg["height_tol"])
    base_tol = float(pole_cfg["base_tol"])
    pairs = []
    for i, left in enumerate(poles):
        for right in poles[i + 1:]:
            separation = float(np.linalg.norm(left.center_xy - right.center_xy))
            if not width_lo <= separation <= width_hi:
                continue
            if abs(left.height - right.height) > height_tol:
                continue
            if abs(left.base_z - right.base_z) > base_tol:
                continue
            pairs.append((left, right))
    return pairs


def find_poles_neighbor(xyz: np.ndarray, pole_cfg: dict,
                        neighbor_radius: float = 0.8,
                        diagnostics: dict | None = None,
                        crossbar_z: tuple[float, float] | list[float] | None = None,
                        base_z_hint: float | None = None
                        ) -> list[VerticalPole]:
    """Layer-aware local fallback for grid-boundary failures.

    A raw 3-D connected component is the wrong primitive for a goal: the
    crossbar intentionally connects both posts and turns the complete frame
    into one wide component.  Connectivity is therefore built only from the
    lower (post-body) height layer.  Each narrow seed component then claims
    full-height points by XY distance, which restores the post tops without
    allowing the horizontal crossbar to bridge the two posts.

    Returned ``indices`` always address the original ``xyz`` array.  This is
    important because detector provenance must survive the fallback exactly
    like it does for the regular grid path.
    """
    points = np.asarray(xyz, dtype=np.float64)
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update({"input_points": int(len(points)), "components": 0,
                            "seed_points": 0, "crossbar_layer_points": 0,
                            "unassigned_points": 0, "output_poles": 0,
                            "reason_counts": {}})
    if len(points) == 0:
        return []
    radius = float(neighbor_radius)
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("neighbor_radius 必须为正数")
    split_z = (float(crossbar_z[0]) if crossbar_z is not None
               else float(pole_cfg["height"][0]))
    seed_indices = np.flatnonzero(points[:, 2] < split_z)
    if diagnostics is not None:
        diagnostics["seed_points"] = int(len(seed_indices))
        diagnostics["crossbar_layer_points"] = int(len(points) - len(seed_indices))
        diagnostics["layer_split_z"] = split_z
    reasons = diagnostics["reason_counts"] if diagnostics is not None else {}
    if len(seed_indices) == 0:
        reasons["no_post_body_points"] = 1
        return []
    seed_points = points[seed_indices]
    tree = cKDTree(seed_points)
    neighbors = tree.query_ball_tree(tree, radius)
    visited = np.zeros(len(seed_points), dtype=bool)
    components = []
    for root in range(len(seed_points)):
        if visited[root]:
            continue
        stack = [root]
        visited[root] = True
        members = []
        while stack:
            current = stack.pop()
            members.append(current)
            for other in neighbors[current]:
                if not visited[other]:
                    visited[other] = True
                    stack.append(other)
        components.append(seed_indices[np.asarray(members, dtype=np.int64)])
    if diagnostics is not None:
        diagnostics["components"] = len(components)
    min_height = float(pole_cfg["height"][0])
    max_height = float(pole_cfg["height"][1])
    max_radius = float(pole_cfg["max_radius"])
    min_points = int(pole_cfg["min_points"])
    min_seed_points = int(pole_cfg.get("neighbor_min_seed_points",
                                       max(2, min_points // 2)))
    attach_radius = float(pole_cfg.get("neighbor_xy_radius", radius))
    max_base_gap = float(pole_cfg.get("neighbor_max_base_gap", 0.0))
    if not np.isfinite(attach_radius) or attach_radius <= 0:
        raise ValueError("neighbor_xy_radius 必须为正数")
    poles = []
    claimed = np.zeros(len(points), dtype=bool)
    # Larger/taller body seeds claim first.  This makes ownership deterministic
    # when clutter lies within the attach radius of more than one seed.
    components.sort(key=lambda members: (
        float(np.ptp(points[members, 2])) if len(members) else 0.0,
        len(members)), reverse=True)
    for seed_members in components:
        if len(seed_members) < min_seed_points:
            reasons["insufficient_seed_points"] = reasons.get(
                "insufficient_seed_points", 0) + 1
            continue
        seed_block = points[seed_members]
        seed_center = np.median(seed_block[:, :2], axis=0)
        seed_spread = np.linalg.norm(seed_block[:, :2] - seed_center, axis=1)
        if float(np.sqrt((seed_spread ** 2).mean())) > max_radius:
            reasons["seed_radius_too_large"] = reasons.get(
                "seed_radius_too_large", 0) + 1
            continue
        xy_distance = np.linalg.norm(points[:, :2] - seed_center, axis=1)
        members = np.flatnonzero((xy_distance <= attach_radius) & ~claimed)
        if len(members) < min_points:
            reasons["insufficient_component_points"] = reasons.get(
                "insufficient_component_points", 0) + 1
            continue
        block = points[members]
        center_xy = block[:, :2].mean(axis=0)
        spread = np.linalg.norm(block[:, :2] - center_xy, axis=1)
        component_radius = float(np.sqrt((spread ** 2).mean()))
        observed_base = float(block[:, 2].min())
        recovered_base = observed_base
        if base_z_hint is not None and np.isfinite(base_z_hint) \
                and 0.0 <= observed_base - float(base_z_hint) <= max_base_gap:
            recovered_base = float(base_z_hint)
        height = float(block[:, 2].max() - recovered_base)
        if component_radius > max_radius:
            reasons["component_radius_too_large"] = reasons.get(
                "component_radius_too_large", 0) + 1
            continue
        if height < min_height:
            reasons["component_height_too_short"] = reasons.get(
                "component_height_too_short", 0) + 1
            continue
        if height > max_height and component_radius > max_radius * 0.5:
            reasons["component_height_too_large_and_wide"] = reasons.get(
                "component_height_too_large_and_wide", 0) + 1
            continue
        claimed[members] = True
        poles.append(VerticalPole(center_xy=center_xy,
                                  base_z=recovered_base,
                                  top_z=float(block[:, 2].max()),
                                  radius=component_radius, count=len(members),
                                  indices=members))
    poles.sort(key=lambda pole: pole.count, reverse=True)
    if diagnostics is not None:
        diagnostics["output_poles"] = len(poles)
        diagnostics["unassigned_points"] = int((~claimed).sum())
        diagnostics["neighbor_xy_radius"] = attach_radius
        diagnostics["base_z_hint"] = base_z_hint
        diagnostics["base_recovered_poles"] = sum(
            int(p.base_z != float(points[p.indices, 2].min())) for p in poles)
    return poles


def crossbar_coverage(xyz: np.ndarray, left: VerticalPole, right: VerticalPole,
                      crossbar_cfg: dict) -> float:
    """Fraction of the span between the poles that has crossbar points.

    Only points strictly between the poles count (pole tops must not vouch for
    their own crossbar), and the sampled line covers the interior 8%–92% of the
    span.  A point counts when it lies within half the z band of the top line.
    Returns 0 when the tops are outside the window (the crossbar cannot be
    there for this pair).
    """
    z_lo, z_hi = (float(v) for v in crossbar_cfg)
    tops_z = (left.top_z + right.top_z) / 2.0
    if not z_lo <= tops_z <= z_hi:
        return 0.0
    band = 0.5 * (z_hi - z_lo)
    direction = right.center_xy - left.center_xy
    length_sq = float(direction @ direction)
    if length_sq <= 0:
        return 0.0
    offsets = xyz[:, :2] - left.center_xy[None, :]
    projection = (offsets @ direction) / length_sq
    lateral = offsets - projection[:, None] * direction[None, :]
    interior = ((projection > 0.02) & (projection < 0.98)
                & (np.linalg.norm(lateral, axis=1) <= band * 2.0)
                & (xyz[:, 2] >= tops_z - band) & (xyz[:, 2] <= tops_z + band))
    in_band = xyz[interior]
    if len(in_band) == 0:
        return 0.0
    from scipy.spatial import cKDTree
    samples = np.linspace(0.08, 0.92, 11)
    line_xy = left.center_xy[None, :] + samples[:, None] * direction[None, :]
    distances, _ = cKDTree(in_band[:, :2]).query(line_xy, k=1)
    return float((distances <= band).mean())


def bbox_for_pair(left: VerticalPole, right: VerticalPole) -> dict:
    """Axis information for the goal frame: centre, size, yaw (radians)."""
    center_xy = (left.center_xy + right.center_xy) / 2.0
    direction = right.center_xy - left.center_xy
    yaw = float(np.arctan2(direction[1], direction[0]))
    separation = float(np.linalg.norm(direction))
    depth = 2.0 * max(left.radius, right.radius)
    height = (left.top_z + right.top_z) / 2.0 - min(left.base_z, right.base_z)
    box = {
        "center": [float(center_xy[0]), float(center_xy[1]),
                   float(min(left.base_z, right.base_z) + height / 2.0)],
        "size": [depth, separation, height],
        "yaw": yaw,
    }
    box["corners"] = oriented_box_corners(box["center"], box["size"], yaw)
    box["minimum"], box["maximum"] = axis_aligned_bounds(box["corners"])
    return box


def oriented_box_corners(center, size, yaw: float) -> list[list[float]]:
    """Eight world-space corners for the goal OBB.

    ``size`` is ``[depth, goal_width, height]``.  ``yaw`` points from the
    left post to the right post, so the width axis follows ``[cos, sin]`` and
    the depth axis is its perpendicular.  This is the same convention used by
    the WebUI's local-Y box after its ``yaw - pi/2`` rotation.
    """
    c = np.asarray(center, dtype=np.float64)
    depth, width, height = (float(v) for v in size)
    width_axis = np.array([np.cos(yaw), np.sin(yaw)], dtype=np.float64)
    depth_axis = np.array([-np.sin(yaw), np.cos(yaw)], dtype=np.float64)
    corners = []
    for depth_sign in (-1.0, 1.0):
        for width_sign in (-1.0, 1.0):
            xy = (c[:2] + depth_sign * 0.5 * depth * depth_axis
                  + width_sign * 0.5 * width * width_axis)
            for height_sign in (-1.0, 1.0):
                corners.append([float(xy[0]), float(xy[1]),
                                float(c[2] + height_sign * 0.5 * height)])
    return corners


def axis_aligned_bounds(corners) -> tuple[list[float], list[float]]:
    points = np.asarray(corners, dtype=np.float64)
    if points.shape != (8, 3) or not np.isfinite(points).all():
        raise ValueError("OBB corners 必须是有限的 (8,3) 数组")
    return ([float(v) for v in points.min(axis=0)],
            [float(v) for v in points.max(axis=0)])
