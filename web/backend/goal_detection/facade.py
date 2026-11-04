"""Facade-based goal measurement inside a manual ROI (strategy ``facade``).

The grid and cluster pole extractors in ``geometry.py`` model the goal as
upright columns and fail on real net returns: the net connects both posts into
one wide 3-D component (``seed_radius_too_large``) and per-cell height gates
cut merged columns apart (``height_too_short``).  This module instead measures
the goal *as a facade*: the structure plane is fit robustly in ground-relative
coordinates, and the left boundary, right boundary and top edge are measured
inside that plane from density and coverage.  No 3-D connectivity is used, so
the net cannot merge the two posts.

Input contract: ground-relative points (``z_leveling`` applied upstream).  The
pipeline skips detection when no trusted ground model exists — height
semantics are undefined otherwise.
"""
from __future__ import annotations

from time import perf_counter

import numpy as np

from .detector import GoalCandidate


class FacadeGoalDetector:
    """Measure the goal as a facade: robust plane, boundary peaks, top line."""

    def __init__(self, detection_cfg: dict):
        self.cfg = detection_cfg["facade"]
        self.scoring = detection_cfg["scoring"]
        self.range_cfg = None
        self.last_rejected: list[GoalCandidate] = []
        self.last_trace: list[dict] = []
        self.last_masks: dict[str, np.ndarray] = {}
        self.last_rejected_summary = {"total": 0, "returned": 0, "truncated": False}

    def set_range(self, range_min: float, range_max: float) -> None:
        self.range_cfg = (float(range_min), float(range_max))

    def detect(self, points: np.ndarray) -> list[GoalCandidate]:
        """Return the facade goal hypothesis, best first.  Empty input -> []."""
        self.last_rejected = []
        self.last_trace = []
        self.last_masks = {}
        self.last_rejected_summary = {"total": 0, "returned": 0, "truncated": False}
        cfg = self.cfg
        points = np.asarray(points)
        if points.ndim != 2 or points.shape[1] < 3 or len(points) == 0:
            self.last_trace = [_stage("input_filter", 0, 0, None, "skipped")]
            return []
        xyz = points[:, :3].astype(np.float64, copy=False)
        started = perf_counter()
        keep = np.isfinite(xyz).all(axis=1)
        if self.range_cfg is not None:
            distance = np.linalg.norm(xyz, axis=1)
            keep &= (distance >= self.range_cfg[0]) & (distance <= self.range_cfg[1])
        z_band = (float(cfg["z_band"][0]), float(cfg["z_band"][1]))
        keep &= (xyz[:, 2] >= z_band[0]) & (xyz[:, 2] <= z_band[1])
        self.last_masks["input_filter"] = keep.copy()
        self.last_trace.append(_stage("input_filter", len(points), int(keep.sum()),
                                      (perf_counter() - started) * 1000.0,
                                      reasons={"z_band": list(z_band),
                                               "range": self.range_cfg}))
        if int(keep.sum()) < int(cfg["min_points"]):
            self.last_trace.append(_stage("facade_fit", int(keep.sum()), 0, None,
                                          "skipped", reason="insufficient_points"))
            return []
        band_xyz = xyz[keep]
        band_index = np.flatnonzero(keep)

        # --- facade plane -------------------------------------------------
        started = perf_counter()
        facade = _fit_facade_axis(band_xyz[:, :2], float(cfg["facade_trim_sigma"]))
        if facade is None:
            self.last_trace.append(_stage("facade_fit", len(band_xyz), 0, None,
                                          "skipped", reason="degenerate_structure_shape"))
            return []
        axis, centroid = facade
        perp = np.array([-axis[1], axis[0]], dtype=np.float64)
        offset = band_xyz[:, :2] - centroid
        u = offset @ axis
        lateral = offset @ perp
        structure = np.abs(lateral) <= float(cfg["lateral_max"])
        mask = np.zeros(len(points), dtype=bool)
        mask[band_index[structure]] = True
        self.last_masks["structure"] = mask
        self.last_trace.append(_stage(
            "facade_fit", len(band_xyz), int(structure.sum()),
            (perf_counter() - started) * 1000.0,
            axis=[round(float(v), 4) for v in axis],
            lateral_std=round(float(np.std(lateral)), 4),
            major_span=round(float(np.ptp(u)), 3)))
        if int(structure.sum()) < int(cfg["min_points"]):
            self.last_trace.append(_stage("boundary_scan", int(structure.sum()), 0, None,
                                          "skipped", reason="insufficient_structure_points"))
            return []

        # --- boundaries ---------------------------------------------------
        started = perf_counter()
        su = u[structure]
        sz = band_xyz[structure, 2]
        slab = np.abs(lateral[structure])
        edges = {}
        for side in ("left", "right"):
            edge, reason = _scan_edge(su, sz, cfg, side)
            if edge is None:
                self.last_trace.append(_stage("boundary_scan", int(structure.sum()), 0,
                                              (perf_counter() - started) * 1000.0,
                                              "skipped", side=side, reason=reason))
                return []
            edges[side] = edge
        self.last_trace.append(_stage(
            "boundary_scan", int(structure.sum()), 2,
            (perf_counter() - started) * 1000.0,
            left=_edge_trace(edges["left"]), right=_edge_trace(edges["right"])))

        # --- top edge -----------------------------------------------------
        started = perf_counter()
        top, reason = _fit_top(su, sz, edges["left"]["u"], edges["right"]["u"], cfg)
        if top is None:
            self.last_trace.append(_stage("top_scan", int(structure.sum()), 0,
                                          (perf_counter() - started) * 1000.0,
                                          "skipped", reason=reason))
            return self._reject(edges, slab, axis, centroid, band_index, structure,
                                "top_not_found")
        self.last_trace.append(_stage(
            "top_scan", int(structure.sum()), 1,
            (perf_counter() - started) * 1000.0,
            top_z=round(float(top["z"]), 3), coverage=round(float(top["coverage"]), 3),
            residual=round(float(top["residual"]), 3),
            bins=int(top["bins"])))

        # --- measurement --------------------------------------------------
        started = perf_counter()
        width = float(edges["right"]["u"] - edges["left"]["u"])
        height = float(top["z"])
        width_band = (float(cfg["width_band"][0]), float(cfg["width_band"][1]))
        if not width_band[0] <= width <= width_band[1]:
            return self._reject(edges, slab, axis, centroid, band_index, structure,
                                "width_out_of_band", width=round(width, 3))
        if not float(cfg["min_height"]) <= height <= float(cfg["max_height"]):
            return self._reject(edges, slab, axis, centroid, band_index, structure,
                                "height_out_of_band", width=round(width, 3),
                                height=round(height, 3))
        geometry = min(_edge_quality(edges["left"], cfg), _edge_quality(edges["right"], cfg))
        top_score = float(top["coverage"]) * (1.0 - min(
            float(top["residual"]) / float(cfg["top_max_residual"]), 1.0))
        confidence = (float(self.scoring["geometry_weight"]) * geometry
                      + float(self.scoring["crossbar_weight"]) * top_score)
        if confidence < float(self.scoring["min_confidence"]):
            return self._reject(edges, slab, axis, centroid, band_index, structure,
                                "low_confidence", width=round(width, 3),
                                height=round(height, 3),
                                confidence=round(confidence, 3))
        candidate = self._build_candidate(edges, top, width, height, geometry,
                                          top_score, confidence, axis, centroid,
                                          slab, band_index, structure)
        self.last_trace.append(_stage(
            "measurement", int(structure.sum()), 1,
            (perf_counter() - started) * 1000.0,
            width=round(width, 3), height=round(height, 3),
            confidence=round(confidence, 3)))
        return [candidate]

    # ------------------------------------------------------------------
    def _build_candidate(self, edges, top, width, height, geometry, top_score,
                         confidence, axis, centroid, slab, band_index, structure):
        perp = np.array([-axis[1], axis[0]], dtype=np.float64)
        u_mid = 0.5 * (edges["left"]["u"] + edges["right"]["u"])
        center_xy = centroid + axis * u_mid
        depth = float(max(2.0 * float(np.quantile(slab, 0.9)), 0.3))
        candidate = GoalCandidate(
            center=[float(center_xy[0]), float(center_xy[1]), float(height / 2.0)],
            size=[depth, width, height],
            yaw=float(np.arctan2(axis[1], axis[0])),
            confidence=float(np.clip(confidence, 0.0, 1.0)),
            crossbar=float(top["coverage"]), pole_count=2,
            left_pole=self._pole_dict(edges["left"], axis, perp, centroid, height),
            right_pole=self._pole_dict(edges["right"], axis, perp, centroid, height))
        candidate.supporting_point_indices = [int(v) for v in band_index[structure]]
        candidate.score_breakdown = {
            "geometry_score": round(float(geometry), 3),
            "crossbar_score": round(float(top_score), 3),
            "geometry_weight": float(self.scoring["geometry_weight"]),
            "crossbar_weight": float(self.scoring["crossbar_weight"]),
            "facade": {
                "axis": [round(float(v), 4) for v in axis],
                "width": round(float(width), 3),
                "height": round(float(height), 3),
                "left": edges["left"], "right": edges["right"],
                "top": top,
            },
        }
        return candidate

    def _pole_dict(self, edge, axis, perp, centroid, estimated_height) -> dict:
        center_xy = centroid + axis * edge["u"]
        return {"center": [float(center_xy[0]), float(center_xy[1])],
                "base_z": round(float(edge["z_min"]), 3),
                "top_z": round(float(edge["z_max"]), 3),
                "observed_vertical_span": round(float(edge["span"]), 3),
                "estimated_full_height": round(float(estimated_height), 3),
                "radius": round(float(edge["residual"]), 3),
                "count": int(edge["count"])}

    def _reject(self, edges, slab, axis, centroid, band_index, structure,
                reason, **extra) -> list[GoalCandidate]:
        """Emit a diagnostic rejected candidate when both boundaries were measured."""
        self.last_rejected_summary = {"total": 1, "returned": 1, "truncated": False}
        width = float(edges["right"]["u"] - edges["left"]["u"])
        depth = float(max(2.0 * float(np.quantile(slab, 0.9)), 0.3))
        perp = np.array([-axis[1], axis[0]], dtype=np.float64)
        u_mid = 0.5 * (edges["left"]["u"] + edges["right"]["u"])
        center_xy = centroid + axis * u_mid
        top_z = max(edges["left"]["z_max"], edges["right"]["z_max"])
        base_z = min(edges["left"]["z_min"], edges["right"]["z_min"])
        candidate = GoalCandidate(
            center=[float(center_xy[0]), float(center_xy[1]),
                    float((top_z + base_z) / 2.0)],
            size=[depth, width, float(top_z - base_z)],
            yaw=float(np.arctan2(axis[1], axis[0])),
            confidence=0.0, crossbar=0.0, pole_count=2,
            left_pole=self._pole_dict(edges["left"], axis, perp, centroid, float(top_z)),
            right_pole=self._pole_dict(edges["right"], axis, perp, centroid, float(top_z)),
            accepted=False, reject_reason=reason)
        candidate.supporting_point_indices = [int(v) for v in band_index[structure]]
        candidate.score_breakdown = {"facade": {"width": round(width, 3), **extra}}
        self.last_rejected = [candidate]
        return []


def propose_regions(xy: np.ndarray, cfg: dict) -> list[np.ndarray]:
    """Cluster vertical-structure points into candidate facade regions.

    Full-field proposal (G3 direction): occupied 2-D grid cells are merged by
    8-connectivity into regions; each region is bounded by the physical width
    budget.  Regions wider than ``proposal_max_span`` (continuous fences or
    walls, including any goal connected to them) are skipped with the reason
    recorded in the returned diagnostics-free list — the caller traces them.
    """
    if len(xy) == 0:
        return []
    cell = float(cfg["proposal_cell_size"])
    min_points = int(cfg["proposal_min_points"])
    max_span = float(cfg["proposal_max_span"])
    max_regions = int(cfg["proposal_max_regions"])
    keys = np.floor(xy / cell).astype(np.int64)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    sorted_keys = keys[order]
    starts = np.concatenate(([0], np.flatnonzero(
        np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1)) + 1))
    ends = np.concatenate((starts[1:], [len(order)]))
    cell_members = {tuple(int(v) for v in sorted_keys[s]): order[s:e]
                    for s, e in zip(starts, ends)}
    parent = {key: key for key in cell_members}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for (gx, gy) in list(cell_members):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                other = (gx + dx, gy + dy)
                if other in cell_members:
                    a, b = find((gx, gy)), find(other)
                    if a != b:
                        parent[b] = a
    regions: dict[tuple, list[np.ndarray]] = {}
    for key, members in cell_members.items():
        regions.setdefault(find(key), []).append(members)
    candidates = []
    for members in regions.values():
        indices = np.concatenate(members)
        if len(indices) < min_points:
            continue
        block = xy[indices]
        span = float(max(np.ptp(block[:, 0]), np.ptp(block[:, 1])))
        if span > max_span:
            continue
        candidates.append((len(indices), indices))
    candidates.sort(key=lambda item: -item[0])
    return [indices for _, indices in candidates[:max_regions]]


def _fit_facade_axis(xy: np.ndarray, trim_sigma: float):
    """Robust dominant horizontal direction of the structure (2-D PCA + trim).

    Returns the unit axis and the centroid of the full input set; the caller
    projects every point itself so array alignment stays explicit.
    """
    if len(xy) < 3:
        return None
    centroid = xy.mean(axis=0)
    points = xy - centroid
    axis = None
    for _ in range(3):
        cov = points.T @ points / len(points)
        values, vectors = np.linalg.eigh(cov)
        axis = vectors[:, int(np.argmax(values))]
        lateral = points @ np.array([-axis[1], axis[0]], dtype=np.float64)
        med = float(np.median(lateral))
        mad = float(np.median(np.abs(lateral - med)))
        limit = max(trim_sigma * 1.4826 * mad, 0.5)
        inliers = np.abs(lateral - med) <= limit
        if inliers.all():
            break
        points = points[inliers]
        if len(points) < 3:
            return None
    if axis is None or not np.isfinite(axis).all():
        return None
    return axis, centroid


def _scan_edge(u: np.ndarray, z: np.ndarray, cfg: dict, side: str):
    """Find one boundary from outside inward on the facade u axis."""
    bin_size = float(cfg["edge_bin_size"])
    min_points = int(cfg["edge_bin_min_points"])
    continuity = int(cfg["edge_continuity_bins"])
    first_bin = int(np.floor(u.min() / bin_size))
    last_bin = int(np.ceil(u.max() / bin_size))
    if last_bin <= first_bin:
        return None, "degenerate_u_span"
    counts = np.bincount(
        np.clip(((u - first_bin * bin_size) / bin_size).astype(np.int64),
                0, last_bin - first_bin),
        minlength=last_bin - first_bin + 1)
    centers = (np.arange(first_bin, last_bin + 1) + 0.5) * bin_size
    supported = np.flatnonzero(counts >= min_points)
    if len(supported) < continuity + 1:
        return None, "no_boundary_support"
    run = None
    if side == "left":
        for pos in range(len(supported) - continuity):
            window = supported[pos:pos + continuity + 1]
            if np.all(np.diff(window) == 1):
                run = int(window[0])
                break
    else:
        for pos in range(len(supported) - continuity - 1, -1, -1):
            window = supported[pos:pos + continuity + 1]
            if np.all(np.diff(window) == 1):
                run = int(window[-1])
                break
    if run is None:
        return None, "no_contiguous_boundary"
    u_raw = float(centers[run])
    sel = np.abs(u - u_raw) <= float(cfg["edge_fit_band"])
    if int(sel.sum()) < int(cfg["edge_min_points"]):
        return None, "edge_insufficient_points"
    u_edge = float(np.median(u[sel]))
    residual = float(1.4826 * np.median(np.abs(u[sel] - u_edge)))
    span = float(z[sel].max() - z[sel].min())
    inlier_fraction = float(np.mean(np.abs(u[sel] - u_edge)
                                    <= float(cfg["edge_inlier_band"])))
    if span < float(cfg["edge_min_height"]):
        return None, "edge_height_too_short"
    if residual > float(cfg["edge_max_residual"]):
        return None, "edge_residual_too_large"
    if inlier_fraction < float(cfg["edge_min_inlier"]):
        # A post is a density concentration; a uniform net or fence edge cut
        # is not.  This gate keeps a sheared net boundary from posing as a
        # missing post.
        return None, "edge_not_concentrated"
    return {"u": u_edge, "u_raw": u_raw, "count": int(sel.sum()),
            "span": span, "z_min": float(z[sel].min()), "z_max": float(z[sel].max()),
            "residual": residual, "inlier_fraction": inlier_fraction}, None


def _fit_top(u: np.ndarray, z: np.ndarray, u_left: float, u_right: float, cfg: dict):
    """Robust top line from per-bin maxima between the two boundaries."""
    bin_size = float(cfg["edge_bin_size"])
    margin = float(cfg["top_margin"])
    lo, hi = u_left + margin, u_right - margin
    if hi <= lo:
        return None, "top_span_empty"
    first_bin = int(np.floor(lo / bin_size))
    last_bin = int(np.ceil(hi / bin_size))
    bins = ((u - first_bin * bin_size) / bin_size).astype(np.int64)
    valid = (bins >= 0) & (bins <= last_bin - first_bin)
    if not valid.any():
        return None, "top_span_empty"
    counts = np.bincount(bins[valid], minlength=last_bin - first_bin + 1)
    tops = np.full(last_bin - first_bin + 1, -np.inf)
    np.maximum.at(tops, bins[valid], z[valid])
    supported = (counts >= int(cfg["top_bin_min_points"])) & np.isfinite(tops)
    if int(supported.sum()) < int(cfg["top_min_bins"]):
        return None, "top_insufficient_bins"
    values = tops[supported]
    level = float(np.median(values))
    dev = values - level
    coverage = float(np.mean(np.abs(dev) <= float(cfg["top_tol"])))
    residual = float(1.4826 * np.median(np.abs(dev)))
    if abs(level - float(cfg["z_band"][1])) <= float(cfg["top_tol"]):
        # The "top" coincides with the measurement band ceiling: the structure
        # continues above it, so this is a truncation artifact (wall, fence,
        # tree), not a crossbar.
        return None, "top_clipped_by_band"
    if coverage < float(cfg["top_min_coverage"]):
        return None, "top_coverage_low"
    if residual > float(cfg["top_max_residual"]):
        return None, "top_residual_high"
    return {"z": level, "coverage": coverage, "residual": residual,
            "bins": int(supported.sum())}, None


def _edge_quality(edge: dict, cfg: dict) -> float:
    span_term = min(edge["span"] / float(cfg["edge_min_height"]), 1.0)
    return 0.5 * span_term + 0.5 * float(edge["inlier_fraction"])


def _edge_trace(edge: dict) -> dict:
    return {key: (round(float(value), 3) if isinstance(value, float) else value)
            for key, value in edge.items()}


def _stage(stage: str, input_points: int, output_points: int,
           latency_ms: float | None, status: str = "completed", **extra) -> dict:
    return {
        "stage": stage, "status": status, "entity": "point",
        "input_points": int(input_points), "output_points": int(output_points),
        "rejected_points": max(int(input_points) - int(output_points), 0),
        "latency_ms": None if latency_ms is None else round(float(latency_ms), 3),
        **extra,
    }
