"""Stage: neighbourhood PCA linearity proposals (W4-A contender).

Seeds are the strongest-valid-return points (top-K by range). Each seed
collects its k nearest valid neighbours; the SVD linearity score
lambda_1 / (lambda_1 + lambda_2 + lambda_3) measures 1-D-ness. Regions whose
linearity meets the threshold and whose dominant-axis extent is at least
min_extent_m become one Proposal. Seeds that land inside an already emitted
region are skipped, keeping deterministic order.

Per NA70_WIRE_ALGORITHMS §5, insufficient-neighbourhood cases are reported
rather than scored: a region with < min_points points is dropped with a
record in the module's last-report field (but not in the Proposal itself;
the pipeline surfaces these via stage counts).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..observations import RayBatch
from .proposals import Proposal


@dataclass(frozen=True)
class PcaConfig:
    neighbour_count: int = 24
    min_points: int = 8
    min_linearity: float = 0.90
    min_extent_m: float = 6.0
    max_regions: int = 16
    top_seed_count: int = 256
    region_merge_distance_m: float = 3.0


def validate_pca_config(cfg: PcaConfig) -> PcaConfig:
    for key in ("neighbour_count", "min_points", "max_regions", "top_seed_count"):
        value = getattr(cfg, key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if cfg.neighbour_count < cfg.min_points:
        raise ValueError("neighbour_count must be >= min_points")
    if not 0.0 < cfg.min_linearity <= 1.0:
        raise ValueError("min_linearity must be in (0, 1]")
    if cfg.min_extent_m <= 0 or cfg.region_merge_distance_m <= 0:
        raise ValueError("extent/merge distances must be positive")
    return cfg


@dataclass(frozen=True)
class PcaReport:
    insufficient_neighbourhood: int
    below_linearity: int
    below_extent: int
    merged_into_existing: int
    emitted: int


def propose_pca(batch: RayBatch, cfg: PcaConfig):
    """Return (proposals, PcaReport); deterministic given (batch, cfg)."""
    cfg = validate_pca_config(cfg)
    valid = batch.valid_return & batch.direction_known
    if not valid.any():
        return tuple(), PcaReport(0, 0, 0, 0, 0)
    xyz = batch.points[valid, :3]
    ranges = np.linalg.norm(xyz, axis=1)
    order = np.argsort(-ranges)[: cfg.top_seed_count]

    batch_positions = np.flatnonzero(valid)
    emitted: list[Proposal] = []
    insufficient = below_lin = below_ext = merged = 0
    used = np.zeros(batch.ray_count, dtype=bool)

    for seed_slot in order:
        if len(emitted) >= cfg.max_regions:
            break
        seed_idx = batch_positions[seed_slot]
        if used[seed_idx]:
            continue
        distances = np.linalg.norm(xyz - xyz[seed_slot], axis=1)
        knn = np.argsort(distances)[: cfg.neighbour_count]
        region = xyz[knn]
        if len(region) < cfg.min_points:
            insufficient += 1
            continue
        centred = region - region.mean(axis=0)
        cov = centred.T @ centred / max(1, len(centred))
        evals = np.linalg.eigvalsh(cov)
        total = float(evals.sum())
        linearity = float(evals.max() / total) if total > 0 else 0.0
        if linearity < cfg.min_linearity:
            below_lin += 1
            continue
        _, _, vh = np.linalg.svd(centred, full_matrices=False)
        dominant = vh[0]
        projections = centred @ dominant
        extent = float(projections.max() - projections.min())
        if extent < cfg.min_extent_m:
            below_ext += 1
            continue
        ids = batch_positions[knn]
        if used[ids].any():
            merged += 1
            continue
        mask = np.zeros(batch.ray_count, dtype=bool)
        mask[ids] = True
        amin = region.min(axis=0)
        amax = region.max(axis=0)
        emitted.append(Proposal(kind="pca_linearity",
                                aabb_m=np.array([amin[0], amax[0],
                                                 amin[1], amax[1],
                                                 amin[2], amax[2]]),
                                mask=mask))
        used[ids] = True
    report = PcaReport(insufficient, below_lin, below_ext, merged, len(emitted))
    return tuple(emitted), report
