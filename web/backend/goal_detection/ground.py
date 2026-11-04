"""Validated local ground reference. No point removal or pose estimation."""
from time import perf_counter

import numpy as np


def estimate_ground(points, accumulated, cfg, settings, source_context=None):
    started = perf_counter()
    if not settings.get('enabled', False):
        return {'enabled': False, 'reason': 'disabled', 'latency_ms': None}
    minimum = int(settings['min_points'])
    context = dict(source_context or {})
    attempts = []
    limits = cfg['accumulation']['range']
    reference_roi = settings.get('reference_roi', 'detection')
    roi = cfg['accumulation'].get('roi') if reference_roi == 'detection' else None
    result = {}
    for role, cloud in (('single', points), ('accumulated', accumulated)):
        result = {'enabled': False, 'source': role, 'input_points': 0,
                  'reference_roi': reference_roi}
        if role == 'single' and context.get('source_frame_range'):
            current = context['source_frame_range'][-1]
            result.update(source_frame_range=[current, current], source_age_seconds=0.0,
                          motion_status='single_frame')
        if role == 'accumulated':
            result.update(context)
            motion = context.get('motion_status', 'unknown')
            age = context.get('source_age_seconds')
            if motion != 'confirmed_static':
                reason = 'moving_ground_window' if motion == 'moving' else 'unknown_ground_motion'
            elif age is None or not np.isfinite(age) or age < 0:
                reason = 'invalid_ground_time'
            elif age > settings['max_source_age_seconds']:
                reason = 'stale_ground_window'
            else:
                reason = None
            if reason:
                result['reason'] = reason
                attempts.append(dict(result))
                continue
        array = np.asarray(cloud() if callable(cloud) else cloud)
        if array.ndim != 2 or array.shape[1] < 3:
            result['reason'] = 'invalid_input'
        else:
            xyz = array[:, :3].astype(np.float64, copy=False)
            keep = np.isfinite(xyz).all(axis=1)
            distance = np.linalg.norm(xyz, axis=1)
            keep &= (distance >= limits['min']) & (distance <= limits['max'])
            if roi is not None:
                bounds = np.asarray(roi).reshape(3, 2)
                keep &= np.all((xyz >= bounds[:, 0]) & (xyz <= bounds[:, 1]), axis=1)
            xyz = xyz[keep]
            result['input_points'] = len(xyz)
            if len(xyz) < minimum:
                result['reason'] = 'insufficient_ground_points'
            else:
                cutoff = np.quantile(xyz[:, 2], settings['fit_quantile'])
                ground = xyz[xyz[:, 2] <= cutoff]
                result.update(_fit(ground, xyz, settings))
        attempts.append(dict(result))
        if result['enabled']:
            break
    # If fallback had no points, retain the meaningful current-source failure.
    if not result['enabled'] and not callable(accumulated) and len(np.asarray(accumulated)) == 0 and attempts:
        result = dict(attempts[0])
    result.update(source_attempts=attempts,
                  height_reference='ground_relative_z' if result['enabled'] else 'sensor_z',
                  latency_ms=round((perf_counter()-started)*1000, 3))
    return result


def _fit(ground, all_points, settings):
    minimum = int(settings['min_points'])
    result = {'enabled': False, 'input_points': len(ground)}
    if len(ground) < minimum:
        return {**result, 'reason': 'insufficient_ground_band'}
    # Center XY before solving so the rank/conditioning test is independent
    # of a target's distance from the sensor origin.
    origin = ground[:, :2].mean(axis=0)
    xy = ground[:, :2] - origin
    design = np.column_stack((xy, np.ones(len(ground))))
    coeff, _, rank, singular = np.linalg.lstsq(design, ground[:, 2], rcond=None)
    result['rank'] = int(rank)
    if rank < 3:
        return {**result, 'reason': 'degenerate_ground_plane'}
    residual = ground[:, 2] - design @ coeff
    # An absolute residual bound cannot grow with contaminated input MAD.
    inliers = np.abs(residual - np.median(residual)) <= settings['max_residual']
    if inliers.sum() < minimum:
        return {**result, 'reason': 'insufficient_ground_inliers'}
    ground = ground[inliers]
    xy = ground[:, :2] - origin
    design = np.column_stack((xy, np.ones(len(ground))))
    coeff, _, rank, singular = np.linalg.lstsq(design, ground[:, 2], rcond=None)
    minor_std = np.linalg.svd(xy - xy.mean(axis=0), compute_uv=False)[-1] / np.sqrt(len(xy))
    span = np.ptp(xy, axis=0)
    result.update(rank=int(rank), inlier_points=len(ground),
                  xy_span_m=span.tolist(), xy_minor_std_m=float(minor_std))
    if rank < 3 or minor_std < settings['min_xy_minor_std']:
        return {**result, 'reason': 'degenerate_ground_plane'}
    if np.min(span) < settings['min_xy_span']:
        return {**result, 'reason': 'insufficient_ground_coverage'}
    residual = ground[:, 2] - design @ coeff
    slope = float(np.linalg.norm(coeff[:2]))
    intercept = float(coeff[2] - coeff[:2] @ origin)
    p95 = float(np.quantile(np.abs(residual), .95))
    below = all_points[:, 2] - (all_points[:, :2] @ coeff[:2] + intercept)
    below_fraction = float(np.mean(below < -settings['max_residual']))
    result.update(slope=slope, residual_std=float(np.std(residual)),
                  residual_p95=p95, below_plane_fraction=below_fraction,
                  slope_x=float(coeff[0]), slope_y=float(coeff[1]), intercept=intercept)
    if slope > settings['max_abs_slope'] or p95 > settings['max_residual']:
        return {**result, 'reason': 'ground_plane_untrusted'}
    if below_fraction > settings['max_below_plane_fraction']:
        return {**result, 'reason': 'points_below_ground_plane'}
    # min_abs_slope is retained only for old configuration compatibility.
    # Horizontal ground still needs its intercept subtracted.
    return {**result, 'enabled': True, 'reason': None}
