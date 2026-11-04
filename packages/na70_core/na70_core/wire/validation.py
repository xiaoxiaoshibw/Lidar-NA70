"""Stage: finite-support validation.

Takes a fit (origin, direction, inlier ray indices into the RayBatch) and
decides whether the line is supported by a finite, actually-measured segment
of the wire - not by duplicate rays that repeat the same source packet.

The metrics here go directly into the debug/acceptance contract:
  * unique source packet count;
  * projection span along the line (measured-support span, not endpoint-to-endpoint);
  * coverage as a fraction of evenly spaced bins that contain at least one
    support point;
  * largest internal gap (max distance between consecutive support projections);
  * per-bin lateral spread of support points around the line (a wire's
    support is a thin 1-D manifold; dense blobs like tree canopy have a wide
    lateral spread even when RANSAC threads a line through them);
  * sag amplitude from a quadratic residual fit along the segment. A wire
    hangs in a vertical plane, so the signed residual is projected onto the
    global +Z axis before fitting; wires with sag beyond `max_sag_m` are
    rejected as EXCESSIVE_SAG so a straight line is not silently reported
    where a curve model is required.

Extrapolation beyond the support cluster is intentionally *not* reported:
without an independent edge constraint any extrapolation figure would be a
constant of the bin width, not a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..observations import RayBatch
from .config import ValidationConfig
from .fitting import FitResult


@dataclass(frozen=True)
class FiniteSupportReport:
    ok: bool
    reject_reason: str | None
    reject_counts: dict
    unique_packet_count: int
    unique_frame_count: int
    span_m: float
    span_bin_m: float
    coverage_fraction: float
    largest_internal_gap_m: float
    max_bin_lateral_spread_m: float | None
    sag_amplitude_m: float | None
    support_density_per_m: float | None
    rms_orthogonal_m: float | None
    p95_orthogonal_m: float | None
    nearest_distance_m: float | None
    source_timestamp_last_s: float | None
    window_age_s: float | None
    segment_sensor: tuple | None  # (s_min_point_m, s_max_point_m)


def _bin_lateral_spreads(perp: np.ndarray, bin_idx: np.ndarray, n_bins: int) -> np.ndarray:
    """Coordinate-range spread of residual vectors per occupied bin."""
    spreads = np.zeros(n_bins)
    for b in range(n_bins):
        members = perp[bin_idx == b]
        if len(members) < 2:
            continue
        ranges = members.max(axis=0) - members.min(axis=0)
        spreads[b] = float(np.linalg.norm(ranges))
    return spreads


def _sag_amplitude(signed_z_residual: np.ndarray, u: np.ndarray,
                   span_m: float) -> float | None:
    """Least-squares amplitude of residual ≈ c + 4*s*u*(1-u) along the segment."""
    if span_m <= 0 or signed_z_residual.size < 3:
        return None
    basis = np.column_stack([np.ones_like(u), 4.0 * u * (1.0 - u)])
    coeffs, *_ = np.linalg.lstsq(basis, signed_z_residual, rcond=None)
    if not np.isfinite(coeffs).all():
        return None
    return float(abs(coeffs[1]))


def evaluate_finite_support(batch: RayBatch, fit: FitResult,
                            cfg: ValidationConfig,
                            window_end_time_s: float | None) -> FiniteSupportReport:
    if not fit.ok or fit.direction is None or fit.point_on_line is None:
        counts = {fit.reject_reason: 1} if fit.reject_reason else {}
        return FiniteSupportReport(False, fit.reject_reason, counts, 0, 0, 0.0,
                                   cfg.span_bin_m, 0.0, 0.0,
                                   None, None, None, None, None, None, None,
                                   None, None)
    idx = fit.inlier_ray_indices
    xyz = batch.points[idx, :3]
    d = fit.direction
    origin = fit.point_on_line

    along = (xyz - origin) @ d
    span = float(along.max() - along.min()) if along.size else 0.0
    projections = origin + along[:, None] * d[None, :]
    perp = xyz - projections

    packets = np.unique(batch.packet_indices[idx])
    frames = np.unique(batch.frame_indices[idx])

    bin_m = cfg.span_bin_m
    n_bins = max(1, int(np.ceil(span / bin_m)))
    occupied = np.zeros(n_bins, dtype=bool)
    bin_idx = np.clip(((along - along.min()) / bin_m).astype(int), 0, n_bins - 1)
    occupied[bin_idx] = True
    coverage = float(occupied.mean()) if n_bins else 0.0
    sorted_along = np.sort(along)
    gaps = np.diff(sorted_along)
    largest_gap = float(gaps.max()) if gaps.size else 0.0

    spreads = _bin_lateral_spreads(perp, bin_idx, n_bins)
    max_lateral = float(spreads.max()) if spreads.size else None
    u = (along - along.min()) / span if span > 0 else np.zeros_like(along)
    sag = _sag_amplitude(perp[:, 2], u, span)
    density = float(len(idx) / span) if span > 0 else None

    rms = fit.rms_orthogonal_m
    p95 = fit.p95_orthogonal_m
    dists = np.linalg.norm(xyz, axis=1)
    nearest = float(dists.min()) if dists.size else None

    times = batch.device_times[idx]
    finite_times = times[np.isfinite(times)]
    last_time = float(finite_times.max()) if finite_times.size else None
    age = None
    if window_end_time_s is not None and last_time is not None:
        age = window_end_time_s - last_time

    reasons = []
    if packets.size < cfg.min_unique_packets: reasons.append("INSUFFICIENT_SUPPORT")
    if span < cfg.min_span_m: reasons.append("INSUFFICIENT_SUPPORT")
    if coverage < cfg.min_coverage_fraction: reasons.append("LOW_COVERAGE")
    if largest_gap > cfg.max_internal_gap_m: reasons.append("LARGE_GAP")
    if max_lateral is not None and max_lateral > cfg.max_bin_lateral_spread_m:
        reasons.append("LATERAL_SPREAD")
    if sag is not None and sag > cfg.max_sag_m:
        reasons.append("EXCESSIVE_SAG")
    # A real wire re-returns from many distinct positions along itself per
    # accumulation window; a few loosely-aligned points spread over tens of
    # metres (e.g. canopy tops) cannot be confirmed as a line.
    if density is not None and span > cfg.min_span_for_density_check_m \
            and density < cfg.min_support_density_per_m:
        reasons.append("SPARSE_SUPPORT")
    if rms is not None and rms > cfg.max_rms_orthogonal_m: reasons.append("FIT_REJECTED")
    if p95 is not None and p95 > cfg.max_p95_orthogonal_m: reasons.append("FIT_REJECTED")
    if age is not None and age > cfg.max_window_age_s: reasons.append("STALE_MEASUREMENT")

    ok = not reasons
    counts = {code: reasons.count(code) for code in reasons}
    seg = None
    if along.size:
        p_min = origin + along.min() * d
        p_max = origin + along.max() * d
        seg = ([float(p_min[0]), float(p_min[1]), float(p_min[2])],
               [float(p_max[0]), float(p_max[1]), float(p_max[2])])

    return FiniteSupportReport(ok, None if ok else ";".join(reasons),
                               counts,
                               int(packets.size), int(frames.size), span, bin_m,
                               coverage, largest_gap, max_lateral, sag, density,
                               rms, p95, nearest, last_time, age, seg)
