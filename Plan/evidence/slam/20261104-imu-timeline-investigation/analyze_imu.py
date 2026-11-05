"""Read-only IMU timeline description; no timing interpolation or position integration."""
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "radar_capture_data/na70_sessions/20261104-170341/imu_frames.jsonl"
rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
times = np.asarray([r["t_unix"] for r in rows], dtype=np.float64)
q = np.asarray([r["quaternion"] for r in rows], dtype=np.float64)
euler = np.asarray([r["euler"] for r in rows], dtype=np.float64)
accel = np.asarray([r["accel"] for r in rows], dtype=np.float64)
gyro = np.asarray([r["gyro"] for r in rows], dtype=np.float64)
assert q.shape == (len(rows), 4) and euler.shape == accel.shape == gyro.shape == (len(rows), 3)
assert np.isfinite(q).all() and np.isfinite(times).all()
assert (np.diff(times) >= 0).all()
norm = np.linalg.norm(q, axis=1)
assert np.all(norm > 0)
q_normalized = q / norm[:, None]
# Sign-invariant relative rotation magnitude, not a world-frame orientation.
angular_distance = 2 * np.degrees(np.arccos(np.clip(np.abs(q_normalized @ q_normalized[0]), 0, 1)))
unique, first, counts = np.unique(times, return_index=True, return_counts=True)
last = first + counts - 1
within_batch_angle = 2 * np.degrees(np.arccos(np.clip(np.abs(np.sum(q_normalized[first] * q_normalized[last], axis=1)), 0, 1)))
digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
summary = {
    "schema": "na70-imu-host-timeline-diagnostic-v1",
    "status": "DIAGNOSTIC_ONLY",
    "source": str(SOURCE.relative_to(ROOT)),
    "source_sha256": digest,
    "frames": len(rows), "time_source": "HOST_RECEIVE_ONLY",
    "host_start_unix_s": float(times[0]), "host_end_unix_s": float(times[-1]),
    "host_span_s": float(times[-1] - times[0]),
    "distinct_host_timestamps": len(unique),
    "batch_frame_count_min_max": [int(counts.min()), int(counts.max())],
    "host_batch_gap_median_max_s": [float(np.median(np.diff(unique))), float(np.max(np.diff(unique)))],
    "quaternion_order_convention": "wxyz, based on existing capture CSV labels; frame direction unverified",
    "quaternion_norm_min_max": [float(norm.min()), float(norm.max())],
    "angular_distance_from_first_deg_max": float(angular_distance.max()),
    "within_batch_first_to_last_orientation_change_deg_max": float(within_batch_angle.max()),
    "euler_recorded_first": euler[0].tolist(), "euler_recorded_last": euler[-1].tolist(),
    "has_any_sample_us": any(r.get("sample_us") is not None for r in rows),
    "has_any_ready_us": any(r.get("ready_us") is not None for r in rows),
    "position_trajectory": "NOT_EVALUATED",
    "limitations": [
        "Frame timestamps are receive completion times shared by each batch; sample times are unknown.",
        "The orientation magnitude is derived from device-fused quaternions and is not independent ground truth.",
        "Quaternion coordinate frame, direction, installation, accelerometer bias and gyro units remain unverified.",
        "No independent position, velocity, timing or bias reference exists for a credible IMU position trajectory.",
    ],
    "environment": {"python": sys.version, "numpy": np.__version__, "platform": platform.platform()},
}
print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
