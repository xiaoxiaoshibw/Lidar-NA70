"""BEV（俯瞰）采集级：纯 XY 平面的球门区域提议（staged 策略第 1 级）。

设计依据（2026-09-18 探针，9.14_caochangyou 20 帧窗口实测）：时变 yaw 使
立柱在 XY 上沿弧线拖尾，三维柱判据（格内 XY 紧致）失效；但立柱格的逐帧
点密度（≈1.0/帧）仍是地面/球网格（≈0.4/帧）的约 2 倍，且两根立柱 blob
相距标准门宽、中间只有低密度填充，而墙体沿跨度密度均匀（对比 ≈ 1）。
因此用「双致密 blob + 跨度密度对比」在纯 XY 平面提议球门区域；确认交给
FOV 裁剪后的三维检测器，本模块不做最终判断。

所有阈值来自 goal_detection.yaml 的 ``bev`` 段；密度统一按窗口帧数归一，
阈值与窗口长度无关。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class BevRegion:
    """A goal-like pair of dense blobs on the ground plane (XY only)."""

    center_xy: np.ndarray
    left_xy: np.ndarray
    right_xy: np.ndarray
    width: float
    yaw: float
    score: float
    peak_density: float   # 两侧 blob 峰值密度（逐帧）的较小值
    contrast: float       # peak_density / 跨度中位密度

    def as_dict(self) -> dict:
        return {
            "center": [round(float(self.center_xy[0]), 2), round(float(self.center_xy[1]), 2)],
            "width": round(float(self.width), 2),
            "yaw": round(float(self.yaw), 3),
            "score": round(float(self.score), 3),
            "peak_density": round(float(self.peak_density), 3),
            "contrast": round(float(self.contrast), 2),
        }


def bev_regions(xyz: np.ndarray, frames_in_window: int, bev_cfg: dict,
                frame_cfg: dict, diagnostics: dict | None = None) -> list[BevRegion]:
    """Propose goal-like XY regions from an accumulated cloud, best first."""
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update({"reason_counts": {}, "input_points": int(len(xyz)),
                            "dense_cells": 0, "blob_count": 0,
                            "pair_count": 0, "output_regions": 0})
    reasons = diagnostics["reason_counts"] if diagnostics is not None else {}
    frames_in_window = int(frames_in_window)
    if len(xyz) < 2 or frames_in_window < int(bev_cfg.get("min_window_frames", 5)):
        reasons["insufficient_window_or_points"] = 1
        return []
    cell = float(bev_cfg["cell_size"])
    points = np.asarray(xyz)
    original_count = len(points)
    finite_mask = np.isfinite(points[:, :3]).all(axis=1)
    points = points[finite_mask]
    if original_count != len(points):
        reasons["non_finite_points"] = original_count - len(points)
    if len(points) < 2:
        return []
    xy = points[:, :2]
    keys = np.floor(xy / cell).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    counts = np.bincount(inverse, minlength=len(unique)).astype(np.float64)
    density = counts / float(frames_in_window)
    min_density = float(bev_cfg["min_cell_density"])
    # 窗口刚填满时绝对点数少，泊松涨落能造出孤立「致密格」；密度阈值之外
    # 再要求绝对点数下限，两者取严。
    min_points_abs = int(bev_cfg.get("min_cell_points", 10))
    dense_mask = (density >= min_density) & (counts >= min_points_abs)
    if not dense_mask.any():
        reasons["no_dense_cells"] = 1
        return []
    if diagnostics is not None:
        diagnostics["dense_cells"] = int(dense_mask.sum())
    cells = {tuple(int(v) for v in key): index
             for key, index in zip(unique, range(len(unique)))}
    # 8 邻域并查集：连通分量超过 max_blob_cells 的是墙体/围栏线，整体丢弃。
    parent = {key: key for key, index in cells.items() if dense_mask[index]}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for cx, cy in list(parent):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                other = (cx + dx, cy + dy)
                if other != (cx, cy) and other in parent:
                    ra, rb = find((cx, cy)), find(other)
                    if ra != rb:
                        parent[rb] = ra
    max_cells = int(bev_cfg["max_blob_cells"])
    groups: dict[tuple, list[tuple[int, int]]] = {}
    for key in parent:
        groups.setdefault(find(key), []).append(key)
    def make_blob(member):
        indexes = [cells[key] for key in member]
        weights = density[indexes]
        centers_xy = (np.asarray(member, dtype=np.float64) + 0.5) * cell
        return {
            "centroid": (weights[:, None] * centers_xy).sum(axis=0) / weights.sum(),
            "peak": float(weights.max()),
            "cells": set(member),
        }

    blobs = []
    for member in groups.values():
        if 1 <= len(member) <= max_cells:
            blobs.append(make_blob(member))
            continue
        # 超大分量有两种：均匀墙体/围栏（密度无峰），以及「球网密度链」——
        # 后者会把两根立柱连成一个分量而被误当墙丢弃（2026-09-19 回归测试
        # 抓到，正是真球筐检出时断时续的原因）。取半峰核（密度 ≥ 峰值一半）
        # 重新聚簇：立柱核保留，弱链被剔除；均匀墙体的半峰核仍然过大，维持丢弃。
        peak = max(float(density[cells[key]]) for key in member)
        core = [key for key in member if float(density[cells[key]]) >= 0.5 * peak]
        if not 1 <= len(core) <= max_cells:
            reasons["oversized_uniform_blob"] = reasons.get("oversized_uniform_blob", 0) + 1
            continue
        sub_parent = {key: key for key in core}

        def find_sub(key):
            while sub_parent[key] != key:
                sub_parent[key] = sub_parent[sub_parent[key]]
                key = sub_parent[key]
            return key

        for (cx, cy) in core:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    other = (cx + dx, cy + dy)
                    if other != (cx, cy) and other in sub_parent:
                        ra, rb = find_sub((cx, cy)), find_sub(other)
                        if ra != rb:
                            sub_parent[rb] = ra
        sub_groups: dict[tuple, list[tuple[int, int]]] = {}
        for key in core:
            sub_groups.setdefault(find_sub(key), []).append(key)
        for sub in sub_groups.values():
            if 1 <= len(sub) <= max_cells:
                blobs.append(make_blob(sub))
    if len(blobs) < 2:
        reasons["fewer_than_two_blobs"] = 1
        if diagnostics is not None:
            diagnostics["blob_count"] = len(blobs)
        return []
    if diagnostics is not None:
        diagnostics["blob_count"] = len(blobs)

    width_lo, width_hi = (float(v) for v in frame_cfg["width"])
    min_contrast = float(bev_cfg["min_contrast"])
    span_floor = float(bev_cfg.get("span_density_floor", 0.05))
    nms = float(bev_cfg.get("nms_distance", 2.5))
    weights_cfg = bev_cfg.get("score_weights", {})
    w_density = float(weights_cfg.get("density", 0.3))
    w_contrast = float(weights_cfg.get("contrast", 0.4))
    w_width = float(weights_cfg.get("width", 0.3))
    contrast_ref = float(bev_cfg.get("contrast_ref", 2.5))
    width_target = 0.5 * (width_lo + width_hi)

    proposals = []
    for i in range(len(blobs)):
        for j in range(i + 1, len(blobs)):
            if diagnostics is not None:
                diagnostics["pair_count"] += 1
            first, second = blobs[i], blobs[j]
            vector = second["centroid"] - first["centroid"]
            width = float(np.linalg.norm(vector))
            if not width_lo <= width <= width_hi:
                key = "width_too_small" if width < width_lo else "width_too_large"
                reasons[key] = reasons.get(key, 0) + 1
                continue
            peak = min(first["peak"], second["peak"])
            span = _span_densities(first, second, cells, density, cell)
            if not span:
                reasons["empty_span"] = reasons.get("empty_span", 0) + 1
                continue
            median = float(np.median(span))
            contrast = peak / max(median, span_floor)
            if contrast < min_contrast:
                reasons["low_contrast"] = reasons.get("low_contrast", 0) + 1
                continue
            width_score = 1.0 - min(abs(width - width_target) / max(width_target - width_lo, 1e-6), 1.0)
            density_score = min(peak / (2.0 * min_density), 1.0)
            contrast_score = min(contrast / contrast_ref, 1.0)
            score = w_density * density_score + w_contrast * contrast_score + w_width * width_score
            proposals.append(BevRegion(
                center_xy=(first["centroid"] + second["centroid"]) / 2.0,
                left_xy=first["centroid"], right_xy=second["centroid"],
                width=width, yaw=float(np.arctan2(vector[1], vector[0])),
                score=float(score), peak_density=peak, contrast=float(contrast)))
    proposals.sort(key=lambda region: region.score, reverse=True)
    kept: list[BevRegion] = []
    for region in proposals:
        if all(float(np.linalg.norm(region.center_xy - k.center_xy)) > nms for k in kept):
            kept.append(region)
        if len(kept) >= int(bev_cfg["max_regions"]):
            break
    reasons["nms_suppressed"] = max(len(proposals) - len(kept), 0)
    if diagnostics is not None:
        diagnostics["output_regions"] = len(kept)
    return kept


def _span_densities(first: dict, second: dict, cells: dict, density: np.ndarray,
                    cell: float) -> list[float]:
    """Per-cell densities along the open span between two blob centroids.

    样本落进任一 blob 自身格时跳过；缺失格按 0 计（球门门内是空的，
    墙体对之间的墙格则是致密的——这正是对比判据要放大的差异）。
    """
    start, end = first["centroid"], second["centroid"]
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length <= 0:
        return []
    steps = max(4, int(length / (0.5 * cell)))
    values = []
    for t in np.linspace(0.0, 1.0, steps + 1)[1:-1]:
        point = start + t * vector
        key = (int(np.floor(point[0] / cell)), int(np.floor(point[1] / cell)))
        if key in first["cells"] or key in second["cells"]:
            continue
        index = cells.get(key)
        values.append(float(density[index]) if index is not None else 0.0)
    return values
