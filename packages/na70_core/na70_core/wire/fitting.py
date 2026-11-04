"""Stage: line fitting via the pyRANSAC-3D adapter.

Adapter rules (already mentioned in NA70_WIRE_ALGORITHMS §2):
  * fix the Python `random` seed before every fit so concurrent calls cannot
    share global state and runs are reproducible;
  * reject degenerate input early (too few points, non-finite, all-equal);
  * re-fetch the final-line residuals from `result.A`/`result.B` ourselves -
    `Line.distances` attaches to the current iteration and is not the best
    model's residuals;
  * return raw source indices into the RayBatch, never the inliers as library
    indices.
"""
from __future__ import annotations

from dataclasses import dataclass
import random

import numpy as np

from ..observations import RayBatch
from .config import FitConfig
from .proposals import Proposal


@dataclass(frozen=True)
class FitResult:
    ok: bool
    reject_reason: str | None
    direction: np.ndarray | None
    point_on_line: np.ndarray | None
    inlier_ray_indices: np.ndarray | None
    inlier_source_ids: np.ndarray | None
    rms_orthogonal_m: float | None
    p95_orthogonal_m: float | None
    fitted_ray_count: int
    iteration_count: int


def _selected_points(batch: RayBatch, proposal: Proposal) -> np.ndarray:
    return batch.points[proposal.mask, :3]


def fit_line_ransac(batch: RayBatch, proposal: Proposal, cfg: FitConfig) -> FitResult:
    from pyransac3d import Line  # local import keeps optional dependency lazy

    points = _selected_points(batch, proposal)
    n = points.shape[0]
    if n < cfg.min_points:
        return FitResult(False, "INSUFFICIENT_SUPPORT", None, None, None, None,
                         None, None, n, 0)
    if not np.isfinite(points).all():
        return FitResult(False, "DEGENERATE_SAMPLE", None, None, None, None,
                         None, None, n, 0)
    if np.unique(points, axis=0).shape[0] < 2:
        return FitResult(False, "DEGENERATE_SAMPLE", None, None, None, None,
                         None, None, n, 0)

    # pyRANSAC-3D uses Python's global `random.sample`; fix the seed before
    # every call so concurrent requests cannot interfere with a run.
    random.seed(cfg.fixed_seed)
    np.random.seed(cfg.fixed_seed)
    fitter = Line()
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = fitter.fit(points, thresh=cfg.thresh_m, maxIteration=cfg.max_iteration)
    iteration_used = int(getattr(fitter, "iteration_count", cfg.max_iteration))

    A = np.asarray(result.A, dtype=np.float64)
    B = np.asarray(result.B, dtype=np.float64)
    norm = float(np.linalg.norm(A))
    if not np.isfinite(norm) or norm == 0.0:
        return FitResult(False, "FIT_REJECTED", None, None, None, None,
                         None, None, n, iteration_used)
    direction = A / norm
    point_on_line = B

    # Recompute residuals against the returned best model. The library's
    # `distances` attribute refers to the last iteration, not the best model.
    vecs = points - point_on_line
    ortho = np.linalg.norm(np.cross(vecs, direction), axis=1)
    inlier_mask = ortho <= cfg.thresh_m
    inlier_count = int(inlier_mask.sum())
    if inlier_count < cfg.min_points:
        return FitResult(False, "INSUFFICIENT_SUPPORT", None, None, None, None,
                         None, None, n, iteration_used)

    proposal_positions = np.flatnonzero(proposal.mask)
    inlier_ray_indices = proposal_positions[inlier_mask]
    if inlier_ray_indices.size == 0:
        return FitResult(False, "INSUFFICIENT_SUPPORT", None, None, None, None,
                         None, None, n, iteration_used)

    ortho_in = ortho[inlier_mask]
    rms = float(np.sqrt(np.mean(ortho_in ** 2)))
    p95 = float(np.quantile(ortho_in, 0.95))

    source_ids = np.column_stack((
        batch.frame_indices[inlier_ray_indices],
        batch.packet_indices[inlier_ray_indices],
        batch.point_indices[inlier_ray_indices],
    ))

    return FitResult(True, None, direction, point_on_line,
                     inlier_ray_indices, source_ids, rms, p95, n, iteration_used)


def recompute_inliers(batch: RayBatch, direction, point_on_line, thresh_m: float):
    """Return the full-candidate ortho residual + inlier mask for audit."""
    lines_dir = np.asarray(direction, dtype=np.float64)
    point = np.asarray(point_on_line, dtype=np.float64)
    vecs = batch.points[:, :3] - point
    ortho = np.linalg.norm(np.cross(vecs, lines_dir), axis=1)
    return ortho, ortho <= thresh_m
