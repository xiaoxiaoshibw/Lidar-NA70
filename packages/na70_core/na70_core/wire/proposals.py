"""Stage: proposals. Only manual-ROI proposals are supported in this release.

A proposal selects a subset of rays by a spatial region (AABB in the sensor
coordinate frame). It never alters the underlying RayBatch - selection is
stored as a boolean mask into the batch's ray order. Proposals are inputs to
the fitting stage; fitting only ever sees the proposal's selected rays.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..observations import RayBatch


@dataclass(frozen=True)
class Proposal:
    kind: str
    aabb_m: np.ndarray  # shape (6,) - xmin,xmax,ymin,ymax,zmin,zmax (sensor frame)
    mask: np.ndarray

    @property
    def ray_count(self) -> int:
        return int(self.mask.sum())


def _validate_aabb(aabb) -> np.ndarray:
    arr = np.asarray(aabb, dtype=float)
    if arr.shape != (6,) or not np.isfinite(arr).all():
        raise ValueError("manual ROI must be six finite coordinates")
    if np.any(arr[::2] >= arr[1::2]):
        raise ValueError("manual ROI min must be strictly less than max on each axis")
    return arr


def make_manual_roi_proposal(batch: RayBatch, aabb_m, *, require_valid: bool = True,
                             require_direction_known: bool = True) -> Proposal:
    aabb = _validate_aabb(aabb_m)
    return make_mask_proposal(batch, aabb, _mask_from_aabb(batch, aabb),
                              kind="manual_roi",
                              require_valid=require_valid,
                              require_direction_known=require_direction_known)


def _mask_from_aabb(batch: RayBatch, aabb_m) -> np.ndarray:
    aabb = np.asarray(aabb_m, dtype=float)
    xyz = batch.points[:, :3]
    return np.all((xyz >= aabb[::2]) & (xyz <= aabb[1::2]), axis=1)


def make_mask_proposal(batch: RayBatch, aabb_m, mask, *, kind: str,
                       require_valid: bool = True,
                       require_direction_known: bool = True) -> Proposal:
    """Proposal from an existing mask + precomputed AABB (may be degenerate).

    Full-FOV / proposal-layer callers supply masks whose spatial extent can
    legitimately collapse to a single point; the strict AABB validation used
    for external manual-ROI input would reject those. The zero-size case is
    fine downstream: an empty mask ends at INSUFFICIENT_SUPPORT in the fit
    stage.
    """
    aabb = np.asarray(aabb_m, dtype=float)
    if aabb.shape != (6,) or not np.isfinite(aabb).all():
        raise ValueError("proposal aabb must be six finite coordinates")
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != (batch.ray_count,):
        raise ValueError("mask must be a ray-aligned boolean array")
    if require_valid:
        mask = mask & batch.valid_return
    if require_direction_known:
        mask = mask & batch.direction_known
    return Proposal(kind=kind, aabb_m=aabb, mask=mask)
