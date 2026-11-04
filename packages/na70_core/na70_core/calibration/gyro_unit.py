"""Check the recorded gyro unit against the device's own quaternion output.

The YESENSE stream carries fused orientation (quaternion) next to raw gyro.
Over two host receive batches, the quaternion delta gives an angular rate that
can be compared with the mean gyro over the same interval.  If the recorded
gyro is deg/s the scale is ~1; if it is rad/s the scale is ~57.3.

This is a device-internal consistency check, not an external angular reference:
it verifies unit/axis convention, not gyro scale accuracy or bias.
"""
from __future__ import annotations

import math

import numpy as np


LIMITATIONS = (
    "四元数来自设备融合输出，不是独立角速度基准",
    "主机批量到达时只能用批次代表样本，dt 为批次间隔，不是设备采样间隔",
    "只能检验单位/轴向一致性，不能给出陀螺尺度准确度或零偏",
    "四元数阶数按 (w,x,y,z) 读取；若设备实为其他顺序，本检查会判不一致",
)


def _normalise(q):
    q = np.asarray(q, dtype=np.float64)
    norms = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(norms <= 0) or not np.isfinite(norms).all():
        raise ValueError("quaternions must be finite and non-zero")
    return q / norms


def _multiply(a, b):
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], axis=-1)


def _conjugate(q):
    result = np.array(q, dtype=np.float64, copy=True)
    result[..., 1:] *= -1
    return result


def quaternion_rate(q0, q1, dt, *, frame="body"):
    """Return the incremental angular rate in rad/s for one interval.

    ``frame="body"`` uses ``conj(q0) * q1`` (q maps body to world); ``"world"``
    uses ``q1 * conj(q0)``.  Both are returned by the caller's convention test.
    """
    q0 = _normalise(q0)
    q1 = _normalise(q1)
    dt = np.asarray(dt, dtype=np.float64)
    if not np.isfinite(dt).all() or np.any(dt <= 0):
        raise ValueError("dt must be finite and positive")
    delta = _multiply(_conjugate(q0), q1) if frame == "body" else _multiply(q1, _conjugate(q0))
    delta = delta * np.where(delta[..., 0] < 0, -1.0, 1.0)[..., None]
    vector = delta[..., 1:]
    norm = np.linalg.norm(vector, axis=-1)
    angle = 2.0 * np.arctan2(norm, np.clip(delta[..., 0], -1.0, 1.0))
    safe = np.where(norm > 1e-12, norm, 1.0)
    axis = vector / safe[..., None]
    return axis * (angle / dt)[..., None]


def verify(frames, *, max_dt=0.2, min_rate_deg_s=0.05, stride=1):
    """Compare quaternion-derived rates with recorded gyro over receive batches.

    ``stride`` compares every ``stride``-th batch (1 = adjacent batches).  The
    gyro target is the mean over the whole interval, so longer intervals test a
    smoothed quaternion instead of only its endpoint difference.
    """
    times = np.asarray(frames["times"], dtype=np.float64)
    gyro = np.asarray(frames["gyro"], dtype=np.float64)
    quaternions = frames.get("quaternions")
    if quaternions is None:
        raise ValueError("frames need quaternion output")
    quaternions = _normalise(np.asarray(quaternions, dtype=np.float64))
    if times.ndim != 1 or gyro.shape != (len(times), 3) or quaternions.shape != (len(times), 4):
        raise ValueError("times, gyro and quaternions must be aligned")
    if not (np.isfinite(times).all() and np.isfinite(gyro).all()):
        raise ValueError("non-finite input")
    if np.any(np.diff(times) < 0):
        raise ValueError("host time moved backwards")
    if not math.isfinite(float(max_dt)) or max_dt <= 0:
        raise ValueError("max_dt must be finite and positive")
    if not isinstance(stride, int) or stride < 1:
        raise ValueError("stride must be a positive integer")

    unique, first, counts = np.unique(times, return_index=True, return_counts=True)
    if len(unique) < stride + 3:
        return {"status": "INSUFFICIENT_DATA", "reason": "too few host batches",
                "batches": int(len(unique))}
    last = first + counts - 1
    batch_q = quaternions[last]
    batch_gyro = np.array([gyro[start:stop + 1].mean(axis=0) for start, stop in zip(first, last)])
    cumulative = np.vstack([np.zeros((1, 3)), np.cumsum(batch_gyro, axis=0)])
    left = np.arange(0, len(unique) - stride, stride)
    right = left + stride
    dt = unique[right] - unique[left]
    usable = (dt > 0) & (dt <= max_dt)
    if int(usable.sum()) < 3:
        return {"status": "INSUFFICIENT_DATA", "reason": "fewer than three usable intervals",
                "batches": int(len(unique))}

    target_all = (cumulative[right + 1] - cumulative[left]) / (right - left + 1)[:, None]
    results = {}
    for frame in ("body", "world"):
        omega = np.degrees(quaternion_rate(batch_q[left][usable], batch_q[right][usable],
                                           dt[usable], frame=frame))
        target = target_all[usable]
        norms_q = np.linalg.norm(omega, axis=1)
        norms_g = np.linalg.norm(target, axis=1)
        valid = (norms_q > min_rate_deg_s) & (norms_g > min_rate_deg_s)
        if int(valid.sum()) < 3:
            results[frame] = {"samples": int(valid.sum()), "scale": None, "cosine": None}
            continue
        omega_valid = omega[valid]
        target_valid = target[valid]
        denominator = float(np.sum(target_valid * target_valid))
        scale = float(np.sum(omega_valid * target_valid) / denominator) if denominator > 0 else None
        cosine = float(np.median(
            np.sum(omega_valid * target_valid, axis=1)
            / (np.linalg.norm(omega_valid, axis=1) * np.linalg.norm(target_valid, axis=1))))
        results[frame] = {"samples": int(valid.sum()), "scale": scale, "cosine": cosine,
                          "median_rate_deg_s": float(np.median(norms_g[valid]))}

    body = results["body"]
    world = results["world"]
    chosen = "body" if (body["cosine"] or -1) >= (world["cosine"] or -1) else "world"
    best = results[chosen]
    if best["scale"] is None:
        status = "INSUFFICIENT_DATA"
        reason = "no usable rotation above threshold"
    elif 0.9 <= best["scale"] <= 1.1 and best["cosine"] >= 0.8:
        status = "DEG_S_CONFIRMED"
        reason = "scale near one and direction consistent"
    elif 50.0 <= best["scale"] <= 65.0 and best["cosine"] >= 0.8:
        status = "RAD_S_LIKELY"
        reason = "scale near 57.3; recorded values are likely rad/s"
    else:
        status = "INCONCLUSIVE"
        reason = "scale or direction does not match either unit"
    return {
        "schema": "na70-imu-gyro-unit-v1",
        "status": status,
        "reason": reason,
        "batches": int(len(unique)),
        "stride": int(stride),
        "usable_intervals": int(usable.sum()),
        "median_dt_s": float(np.median(dt[usable])),
        "max_dt_s": float(np.max(dt[usable])),
        "conventions": results,
        "chosen_convention": chosen,
        "scale_deg_per_recorded": best["scale"],
        "cosine": best["cosine"],
        "limitations": list(LIMITATIONS),
        "claims": {"unit_checked_device_internal": True, "external_reference_used": False,
                   "scale_accuracy_validated": False},
    }