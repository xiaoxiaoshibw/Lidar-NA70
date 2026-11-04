"""Static-window, noise-density and overlapping Allan statistics for IMU frames.

The caller declares the units.  Nothing here converts ``deg/s`` to ``rad/s``,
fits a clock, or invents a sample time: the current YESENSE stream exposes only
host receive time, so every interval is a *host* interval unless the caller
supplies verified device times.

Terminology is deliberately narrow:

* ``static_like`` means the window passed a motion screen.  It does not prove
  the rig was physically still, nor that the sensor is healthy.
* ``rate_noise_density`` is ``std * sqrt(dt)`` and is only meaningful if the
  residual is approximately white.  It is not a datasheet noise figure.
* Allan deviation needs a long, gap-free recording; short recordings return a
  table with ``NOT_EVALUATED`` markers rather than a fabricated bias stability.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


LIMITATIONS = (
    "静置一致性只筛除明显运动，不证明物理静止或硬件健康",
    "角速度单位由调用方声明；YESENSE 当前为推断 deg/s，不在此自动换算",
    "噪声密度假设白噪声；未经 Allan 长录验证时不得写成厂家指标",
    "时间为主机接收时间时，所有速率均为主机口径，不是设备采样口径",
)


def _finite_array(values, name, *, ndim):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != ndim:
        raise ValueError(f"{name} must be {ndim}-D")
    if array.size == 0:
        raise ValueError(f"{name} must not be empty")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
    return array


def _vector3(values, name):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3 or len(array) == 0:
        raise ValueError(f"{name} must be a non-empty Nx3 array")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
    return array


def _timeline(times, segments):
    times = _finite_array(times, "times", ndim=1)
    segments = np.asarray(segments)
    if segments.shape != times.shape:
        raise ValueError("segments must match times")
    if segments.dtype.kind not in "iu" or (segments < 0).any():
        raise ValueError("segments must be non-negative integers")
    if len(times) > 1 and np.any(np.diff(times) < 0):
        raise ValueError("host monotonic time moved backwards")
    return times, segments.astype(np.int64)


def static_windows(times, segments, accel, gyro, config):
    """Split by segment and fixed host-time windows; screen each window.

    ``config`` is the JSON object used by ``tools/lio/static_check.yaml``:
    ``window_s``, ``min_samples``, ``min_span_s``, ``max_accel_axis_std_m_s2``,
    ``max_accel_norm_std_m_s2``, ``max_gyro_axis_std_deg_s``,
    ``max_gyro_mean_norm_deg_s``, ``accel_mean_norm_range_m_s2`` and
    ``max_host_gap_s``.  A short trailing window is merged into the previous
    one so that a late movement cannot hide behind a dropped tail.
    """
    times, segments = _timeline(times, segments)
    accel = _vector3(accel, "accel")
    gyro = _vector3(gyro, "gyro")
    if len(accel) != len(times) or len(gyro) != len(times):
        raise ValueError("accel, gyro and times must have equal length")
    window_s = float(config["window_s"])
    if not math.isfinite(window_s) or window_s <= 0:
        raise ValueError("window_s must be finite and positive")
    min_samples = int(config["min_samples"])
    if min_samples <= 0:
        raise ValueError("min_samples must be positive")
    min_span_s = float(config["min_span_s"])
    if not math.isfinite(min_span_s) or min_span_s < 0:
        raise ValueError("min_span_s must be non-negative")
    limits = {
        "max_accel_axis_std_m_s2": float(config["max_accel_axis_std_m_s2"]),
        "max_accel_norm_std_m_s2": float(config["max_accel_norm_std_m_s2"]),
        "max_gyro_axis_std_deg_s": float(config["max_gyro_axis_std_deg_s"]),
        "max_gyro_mean_norm_deg_s": float(config["max_gyro_mean_norm_deg_s"]),
        "max_host_gap_s": float(config["max_host_gap_s"]),
    }
    if not all(math.isfinite(value) and value > 0 for value in limits.values()):
        raise ValueError("static thresholds must be finite and positive")
    norm_range = config["accel_mean_norm_range_m_s2"]
    if (not isinstance(norm_range, (list, tuple)) or len(norm_range) != 2
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in norm_range)
            or not 0 < norm_range[0] < norm_range[1]):
        raise ValueError("accel_mean_norm_range_m_s2 must be an increasing pair")

    first_time = times[0]
    groups: dict[tuple[int, int], list[int]] = {}
    for index, (time, segment) in enumerate(zip(times, segments)):
        key = (int(segment), int((time - first_time) / window_s))
        groups.setdefault(key, []).append(index)

    for segment in sorted({key[0] for key in groups}):
        keys = sorted(key for key in groups if key[0] == segment)
        if len(keys) <= 1:
            continue
        tail = groups[keys[-1]]
        span = times[tail[-1]] - times[tail[0]]
        if len(tail) < min_samples or span < min_span_s:
            groups[keys[-2]].extend(groups.pop(keys[-1]))

    windows = []
    for (segment, number), indices in sorted(groups.items()):
        index = np.asarray(indices, dtype=np.int64)
        span = float(times[index[-1]] - times[index[0]])
        if len(index) < min_samples or span < min_span_s:
            windows.append({"segment": segment, "window": number, "samples": int(len(index)),
                            "host_span_s": span, "status": "INSUFFICIENT_DATA"})
            continue
        acc = accel[index]
        gyr = gyro[index]
        accel_mean = acc.mean(axis=0)
        gyro_mean = gyr.mean(axis=0)
        accel_std = acc.std(axis=0, ddof=1)
        gyro_std = gyr.std(axis=0, ddof=1)
        norms = np.linalg.norm(acc, axis=1)
        accel_mean_norm = float(np.linalg.norm(accel_mean))
        gyro_mean_norm = float(np.linalg.norm(gyro_mean))
        gap = float(np.max(np.diff(times[index]))) if len(index) > 1 else 0.0
        checks = {
            "accel_axis_std": float(accel_std.max()) <= limits["max_accel_axis_std_m_s2"],
            "accel_norm_std": float(norms.std(ddof=1)) <= limits["max_accel_norm_std_m_s2"],
            "gyro_axis_std": float(gyro_std.max()) <= limits["max_gyro_axis_std_deg_s"],
            "gyro_mean_norm": gyro_mean_norm <= limits["max_gyro_mean_norm_deg_s"],
            "accel_mean_norm": float(norm_range[0]) <= accel_mean_norm <= float(norm_range[1]),
            "host_gap": gap <= limits["max_host_gap_s"],
        }
        windows.append({
            "segment": segment,
            "window": number,
            "samples": int(len(index)),
            "host_span_s": span,
            "status": "STATIC_LIKE" if all(checks.values()) else "REJECTED",
            "failed_checks": [name for name, ok in checks.items() if not ok],
            "accel_mean_m_s2": accel_mean.tolist(),
            "accel_std_m_s2": accel_std.tolist(),
            "gyro_mean_deg_s": gyro_mean.tolist(),
            "gyro_std_deg_s": gyro_std.tolist(),
            "accel_mean_norm_m_s2": accel_mean_norm,
            "max_host_gap_s": gap,
        })
    return windows


def overall_status(windows, segments):
    """Aggregate window statuses; any segment break keeps the run non-static."""
    complete = [window for window in windows if window["status"] != "INSUFFICIENT_DATA"]
    if not complete or len(complete) != len(windows):
        return "INSUFFICIENT_DATA"
    if any(window["status"] == "REJECTED" for window in complete) or len(set(segments)) != 1:
        return "REJECTED"
    return "STATIC_LIKE"


def noise_summary(times, gyro, accel, *, units=None):
    """Mean, standard deviation and white-noise density over one array slice.

    The density is ``std * sqrt(dt)`` with ``dt`` the median host interval.  It
    is labelled ``HOST_WHITE_NOISE_ASSUMPTION`` because neither whiteness nor
    the device sample time has been verified.
    """
    times, _ = _timeline(times, np.zeros(len(times), dtype=np.int64))
    gyro = _vector3(gyro, "gyro")
    accel = _vector3(accel, "accel")
    if len(gyro) != len(times) or len(accel) != len(times):
        raise ValueError("gyro, accel and times must have equal length")
    if len(times) < 3:
        raise ValueError("at least three samples are required")
    steps = np.diff(times)
    if np.any(steps <= 0):
        raise ValueError("sample times must strictly increase")
    dt = float(np.median(steps))
    result = {
        "schema": "na70-imu-noise-summary-v1",
        "sample_count": int(len(times)),
        "host_span_s": float(times[-1] - times[0]),
        "median_dt_s": dt,
        "units": units or {"gyro": "declared_by_caller", "accel": "declared_by_caller"},
        "gyro_mean": gyro.mean(axis=0).tolist(),
        "gyro_std": gyro.std(axis=0, ddof=1).tolist(),
        "accel_mean": accel.mean(axis=0).tolist(),
        "accel_std": accel.std(axis=0, ddof=1).tolist(),
        "gyro_rate_noise_density": (gyro.std(axis=0, ddof=1) * math.sqrt(dt)).tolist(),
        "accel_rate_noise_density": (accel.std(axis=0, ddof=1) * math.sqrt(dt)).tolist(),
        "noise_model": "HOST_WHITE_NOISE_ASSUMPTION",
        "limitations": list(LIMITATIONS),
    }
    return result


INT32_FULL_SCALE = 2 ** 31 * 1e-6


def unwrap_microseconds(values):
    """Unwrap a 32-bit microsecond counter to seconds relative to the first sample.

    A decrease is accepted only as the expected 32-bit rollover; any other
    backwards step is rejected.  The result is a *device* counter, not a host
    time and not proof of synchronization.
    """
    values = np.asarray(values)
    if values.ndim != 1 or len(values) == 0:
        raise ValueError("microseconds must be a non-empty 1-D array")
    if values.dtype.kind not in "iu":
        raise ValueError("microseconds must be integers")
    if np.any(values < 0) or np.any(values > 0xFFFFFFFF):
        raise ValueError("microseconds must be unsigned 32-bit values")
    result = np.empty(len(values), dtype=np.float64)
    wraps = 0
    last = None
    for index, value in enumerate(values.tolist()):
        if last is not None and value <= last:
            if last > 0.75 * 2 ** 32 and value < 0.25 * 2 ** 32:
                wraps += 1
            else:
                raise ValueError(f"microseconds went backwards at {index} ({last} -> {value})")
        result[index] = value + wraps * 2 ** 32
        last = value
    result -= result[0]
    result /= 1e6
    return result, wraps


def _runs(mask):
    padded = np.concatenate(([False], np.asarray(mask, dtype=bool), [False]))
    starts = np.flatnonzero(~padded[:-1] & padded[1:])
    ends = np.flatnonzero(padded[:-1] & ~padded[1:]) - 1
    return list(zip(starts.tolist(), ends.tolist()))


def range_summary(times, accel, gyro, *, gyro_range=None, accel_range=None,
                  plateau_samples=3, near_range_fraction=0.98, max_events=50):
    """Observed ranges plus saturation candidates for both sensors.

    Full-scale of the decoded int32 is ``2147.483648`` in declared units.  A
    configured sensor range (if known) is checked at 98% of full scale, and a
    run of identical extreme values is reported as a possible clamp plateau.
    Without a datasheet range this can only flag numeric/plateau evidence, not
    prove the sensor was saturated.
    """
    times = _finite_array(times, "times", ndim=1)
    accel = _vector3(accel, "accel")
    gyro = _vector3(gyro, "gyro")
    if len(accel) != len(times) or len(gyro) != len(times):
        raise ValueError("accel, gyro and times must have equal length")
    if not isinstance(plateau_samples, int) or plateau_samples < 2:
        raise ValueError("plateau_samples must be an integer >= 2")
    if not 0 < near_range_fraction < 1:
        raise ValueError("near_range_fraction must be between 0 and 1")
    for name, value in (("gyro_range", gyro_range), ("accel_range", accel_range)):
        if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be a positive finite number or null")

    channels = {}
    events = []
    for name, values, configured in (("accel", accel, accel_range), ("gyro", gyro, gyro_range)):
        channel = {}
        for axis, axis_name in enumerate("xyz"):
            column = values[:, axis]
            absolute = np.abs(column)
            peak = float(absolute.max())
            channel[axis_name] = {
                "min": float(column.min()), "max": float(column.max()),
                "p01": float(np.percentile(column, 1)), "p99": float(np.percentile(column, 99)),
                "max_abs": peak,
            }
            if peak >= INT32_FULL_SCALE - 1e-9:
                events.append({"sensor": name, "axis": axis_name, "kind": "numeric_full_scale",
                               "peak": peak})
            if configured is not None:
                threshold = float(configured) * near_range_fraction
                for start, end in _runs(absolute >= threshold):
                    events.append({"sensor": name, "axis": axis_name, "kind": "near_configured_range",
                                   "start_s": float(times[start] - times[0]),
                                   "end_s": float(times[end] - times[0]),
                                   "samples": int(end - start + 1), "peak": float(absolute[start:end + 1].max()),
                                   "configured_range": float(configured)})
            if peak > 0 and (peak >= INT32_FULL_SCALE - 1e-9
                             or (configured is not None and peak >= float(configured) * near_range_fraction)):
                for start, end in _runs(absolute == peak):
                    if end - start + 1 >= plateau_samples:
                        events.append({"sensor": name, "axis": axis_name,
                                       "kind": "repeated_extreme_plateau",
                                       "start_s": float(times[start] - times[0]),
                                       "end_s": float(times[end] - times[0]),
                                       "samples": int(end - start + 1), "value": float(column[start])})
        channels[name] = channel

    status = "SATURATION_SUSPECTED" if events else "OK_NO_SATURATION"
    return {
        "schema": "na70-imu-range-v1",
        "status": status,
        "samples": int(len(times)),
        "duration_s": float(times[-1] - times[0]),
        "units": {"gyro": "deg/s (inferred, not converted)", "accel": "m/s^2"},
        "channels": channels,
        "configured_ranges": {"gyro": gyro_range, "accel": accel_range},
        "events": events[:max_events],
        "event_count": len(events),
        "limits": ["未提供厂家量程时只能报告数值满量程/重复极值，不能证明物理饱和",
                   "角速度单位仍为推断 deg/s",
                   "阈值 98% 与平台长度是开发诊断参数，不是厂家指标"],
    }


def batch_average(times, values):
    """Average samples sharing one host receive instant.

    USB reads deliver several frames per host timestamp.  The average is a
    legitimate **host-batch** series, not a device-rate series; it must be
    labelled as such wherever it is used.
    """
    times = _finite_array(times, "times", ndim=1)
    values = np.asarray(values, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, None]
    if values.ndim != 2 or values.shape[0] != len(times):
        raise ValueError("values must be N or NxC matching times")
    if not np.isfinite(values).all():
        raise ValueError("values contain non-finite entries")
    unique, inverse = np.unique(times, return_inverse=True)
    counts = np.bincount(inverse, minlength=len(unique)).astype(np.float64)
    sums = np.zeros((len(unique), values.shape[1]), dtype=np.float64)
    np.add.at(sums, inverse, values)
    return unique, sums / counts[:, None], counts.astype(np.int64)


def host_batch_allan(times, values, *, axis_names=None):
    """Allan deviation of the host-batch-averaged series.

    This can show slow drift over minutes when only host receive time exists,
    but it cannot report device-rate noise or a datasheet noise density.  Large
    arrival gaps split the series into blocks; only the largest contiguous block
    is used, instead of interpolating across a gap.
    """
    unique, means, counts = batch_average(times, values)
    if len(unique) < 8:
        return {"schema": "na70-imu-allan-v1", "status": "INSUFFICIENT_DATA",
                "reason": "fewer than eight host batches", "batches": int(len(unique)),
                "time_basis": "HOST_BATCH_AVERAGED"}
    intervals = np.diff(unique)
    median_interval = float(np.median(intervals))
    breaks = np.flatnonzero(intervals > 1.5 * median_interval) + 1
    edges = [0, *breaks.tolist(), len(unique)]
    blocks = [(edges[index], edges[index + 1]) for index in range(len(edges) - 1)]
    start, stop = max(blocks, key=lambda block: block[1] - block[0])
    if stop - start < 8:
        return {"schema": "na70-imu-allan-v1", "status": "INSUFFICIENT_DATA",
                "reason": "largest contiguous block has fewer than eight batches",
                "batches": int(len(unique)), "blocks": len(blocks),
                "time_basis": "HOST_BATCH_AVERAGED"}
    result = allan_deviation(unique[start:stop], means[start:stop], axis_names=axis_names)
    result["time_basis"] = "HOST_BATCH_AVERAGED"
    result["batches"] = int(len(unique))
    result["blocks"] = len(blocks)
    result["used_batches"] = int(stop - start)
    result["excluded_batches"] = int(len(unique) - (stop - start))
    result["mean_batch_size"] = float(counts.mean())
    result["batch_interval_median_s"] = median_interval
    result["note"] = ("主机批次平均后的 Allan；只反映主机尺度的慢漂移，"
                      "不能当设备采样噪声密度/Allan 指标。大间隙只分段，不插值。")
    result["limitations"] = list(LIMITATIONS) + ["批次平均会削弱高频噪声，tau 下限为批次间隔"]
    return result


def _default_taus(sample_count, dt):
    taus = []
    m = 1
    while m <= max(1, sample_count // 4):
        taus.append(m * dt)
        m *= 2
    if sample_count >= 3:
        largest = (sample_count // 2) * dt
        if not taus or taus[-1] < largest * 0.99:
            taus.append(largest)
    return taus


def allan_deviation(times, values, *, axis_names=None):
    """Overlapping Allan deviation for one or more channels.

    Returns ``taus_s`` and ``adev`` (one array per channel).  ``values`` is
    ``N`` or ``N x C``.  Gaps are rejected rather than resampled: the caller
    must split the recording into contiguous segments first.
    """
    times = _finite_array(times, "times", ndim=1)
    values = np.asarray(values, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, None]
    if values.ndim != 2 or values.shape[0] != len(times):
        raise ValueError("values must be N or NxC matching times")
    if not np.isfinite(values).all():
        raise ValueError("values contain non-finite entries")
    if len(times) < 8:
        raise ValueError("Allan deviation needs at least eight samples")
    steps = np.diff(times)
    if np.any(steps <= 0):
        raise ValueError("sample times must strictly increase")
    dt = float(np.median(steps))
    if np.max(np.abs(steps - dt)) > 0.5 * dt:
        raise ValueError("non-uniform sample interval; split segments before Allan analysis")
    names = list(axis_names) if axis_names else [f"axis{index}" for index in range(values.shape[1])]
    if len(names) != values.shape[1]:
        raise ValueError("axis_names must match the channel count")
    taus = _default_taus(len(times), dt)
    table = []
    for tau in taus:
        m = int(round(tau / dt))
        if m < 1 or m > len(times) // 2:
            continue
        cumulative = np.vstack([np.zeros((1, values.shape[1])), np.cumsum(values, axis=0)])
        means = (cumulative[m:] - cumulative[:-m]) / m
        diffs = means[m:] - means[:-m]
        if len(diffs) == 0:
            continue
        adev = np.sqrt(np.mean(diffs ** 2, axis=0) / 2.0)
        table.append({"tau_s": float(m * dt), "samples_per_cluster": int(m),
                      "pairs": int(len(diffs)),
                      "adev": {name: float(value) for name, value in zip(names, adev)}})
    if not table:
        raise ValueError("record too short for any Allan cluster")
    minima = {}
    for index, name in enumerate(names):
        best = min(table, key=lambda row: row["adev"][name])
        minima[name] = {"adev": best["adev"][name], "tau_s": best["tau_s"]}
    return {
        "schema": "na70-imu-allan-v1",
        "sample_count": int(len(times)),
        "median_dt_s": dt,
        "axes": names,
        "table": table,
        "minimum": minima,
        "status": "EVALUATED" if len(times) >= 10000 else "SHORT_RECORD",
        "note": "短录仅给出 Allan 表；偏置不稳定度需更长无缺口记录并注明上电/温度条件。",
        "limitations": list(LIMITATIONS),
    }