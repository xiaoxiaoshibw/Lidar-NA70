"""Conservative temporal yaw evidence from per-sweep ground returns."""
from __future__ import annotations

import numpy as np

TEMPORAL_YAW_VERSION = "na70-temporal-ground-yaw-v1"


def _circular_median(angles):
    angles = np.asarray(angles, dtype=np.float64)
    if not len(angles):
        return None
    # The circular mean is stable here because the ground band is a narrow
    # local return; unwrap happens only after the per-sweep robust summary.
    return float(np.arctan2(np.median(np.sin(angles)), np.median(np.cos(angles))))


def ground_heading_measurements(sweeps, min_range=10.0, max_range=40.0,
                                ground_band=0.25, min_points=20):
    rows = []
    for sweep in sweeps:
        points = sweep.points(valid_only=True)[:, :3]
        if not len(points):
            continue
        ranges = np.linalg.norm(points, axis=1)
        ground = float(np.median(points[:, 2]))
        mask = ((np.abs(points[:, 2] - ground) <= ground_band)
                & (ranges >= min_range) & (ranges <= max_range))
        selected = points[mask]
        if len(selected) < min_points:
            continue
        heading = _circular_median(np.arctan2(selected[:, 1], selected[:, 0]))
        rows.append(dict(index=sweep.index, time=float(sweep.start_time),
                         heading_rad=heading, point_count=int(len(selected)),
                         ground_z=ground))
    if not rows:
        return []
    values = np.unwrap(np.asarray([row["heading_rad"] for row in rows], dtype=np.float64))
    for row, value in zip(rows, values):
        row["heading_unwrapped_rad"] = float(value)
    return rows


def fit_temporal_yaw(measurements, min_measurements=5):
    if len(measurements) < min_measurements:
        return dict(valid=False, reason="too_few_ground_measurements",
                    measurement_count=len(measurements), yaw_rate_deg_s=None,
                    yaw_start_deg=None, yaw_end_deg=None, residual_mad_deg=None)
    times = np.asarray([row["time"] for row in measurements], dtype=np.float64)
    values = np.asarray([row["heading_unwrapped_rad"] for row in measurements], dtype=np.float64)
    times -= times[0]
    design = np.column_stack((np.ones(len(times)), times))
    weights = np.ones(len(times), dtype=np.float64)
    for _ in range(6):
        solution = np.linalg.lstsq(design * weights[:, None], values * weights, rcond=None)[0]
        residual = values - design @ solution
        scale = 1.4826 * np.median(np.abs(residual - np.median(residual)))
        scale = max(float(scale), np.deg2rad(0.05))
        weights = np.minimum(1.0, 1.5 * scale / np.maximum(np.abs(residual), 1e-12))
    residual = values - design @ solution
    return dict(
        valid=True,
        reason="fitted_ground_heading",
        measurement_count=len(measurements),
        yaw_rate_deg_s=float(np.degrees(solution[1])),
        yaw_start_deg=0.0,
        yaw_end_deg=float(np.degrees(solution[1] * times[-1])),
        residual_mad_deg=float(np.degrees(np.median(np.abs(residual - np.median(residual))))),
        duration_s=float(times[-1]),
        robust_inlier_fraction=float((weights >= 0.5).mean()),
    )


def yaw_offset_at(profile, time_s):
    if not profile.get("valid"):
        return 0.0
    return float(np.deg2rad(profile["yaw_rate_deg_s"] * float(time_s)))

