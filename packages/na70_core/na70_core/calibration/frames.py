"""Read the capture service's ``imu_frames.jsonl`` into plain arrays.

Only schema/order validation happens here.  A missing device timestamp stays
missing: ``t_mono``/``t_unix`` are host receive times and are never promoted to
device sample time.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np


REQUIRED = ("t_mono", "t_unix", "segment", "accel", "gyro")


def load_imu_frames(path, *, start_s=None, end_s=None, segment=None):
    """Load frames, optionally sliced by host time relative to the first frame.

    ``start_s``/``end_s`` are seconds relative to the first accepted sample of
    the selected segment (or of the file when ``segment`` is None).  The slice
    is inclusive at both ends to make 20 s windows intuitive.
    """
    path = Path(path)
    times = []
    unix = []
    segments = []
    accel = []
    gyro = []
    quaternions = []
    quaternion_complete = True
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at row {line_number}: {exc}") from exc
            missing = [key for key in REQUIRED if key not in row]
            if missing:
                raise ValueError(f"row {line_number} missing {','.join(missing)}")
            t, u, s = row["t_mono"], row["t_unix"], row["segment"]
            a, g = row["accel"], row["gyro"]
            if not isinstance(s, int) or s < 0 or len(a) != 3 or len(g) != 3:
                raise ValueError(f"invalid fields at row {line_number}")
            if not all(isinstance(value, (int, float)) and math.isfinite(value)
                       for value in [t, u, *a, *g]):
                raise ValueError(f"non-finite value at row {line_number}")
            q = row.get("quaternion")
            if (not isinstance(q, list) or len(q) != 4
                    or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in q)):
                quaternion_complete = False
                q = [0.0, 0.0, 0.0, 0.0]
            times.append(t)
            unix.append(u)
            segments.append(s)
            accel.append(a)
            gyro.append(g)
            quaternions.append(q)
    if not times:
        raise ValueError("empty IMU frame file")
    times = np.asarray(times, dtype=np.float64)
    unix = np.asarray(unix, dtype=np.float64)
    segments = np.asarray(segments, dtype=np.int64)
    accel = np.asarray(accel, dtype=np.float64)
    gyro = np.asarray(gyro, dtype=np.float64)
    quaternions = np.asarray(quaternions, dtype=np.float64) if quaternion_complete else None
    if np.any(np.diff(times) < 0):
        raise ValueError("host monotonic time moved backwards")
    minimum, maximum = times.min(), times.max()
    result = {"path": str(path), "times": times, "unix": unix, "segments": segments,
              "accel": accel, "gyro": gyro, "quaternions": quaternions}
    if segment is not None:
        if not isinstance(segment, int) or segment < 0:
            raise ValueError("segment must be a non-negative integer")
        if not np.any(segments == segment):
            raise ValueError(f"segment {segment} not present")
        origin = times[segments == segment].min()
    else:
        origin = minimum
    if start_s is None and end_s is None:
        return result
    start = -math.inf if start_s is None else float(start_s)
    end = math.inf if end_s is None else float(end_s)
    if not start < end:
        raise ValueError("start_s must be smaller than end_s")
    relative = times - origin
    mask = (relative >= start) & (relative <= end)
    if segment is not None:
        mask &= segments == segment
    if int(mask.sum()) < 2:
        raise ValueError("time slice contains fewer than two samples")
    for key in ("times", "unix", "segments", "accel", "gyro"):
        result[key] = result[key][mask]
    if result["quaternions"] is not None:
        result["quaternions"] = result["quaternions"][mask]
    result["origin_s"] = float(origin)
    result["slice"] = {"start_s": start_s, "end_s": end_s, "segment": segment}
    return result