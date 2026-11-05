"""Recompute diagnostic input and trajectory correlations, not ground truth."""
import csv
import json
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent / "20261104-lio-diagnostic"
with (BASE / "trajectory.csv").open(newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
t = np.array([float(r["t_s"]) for r in rows])
xyz = np.array([[float(r[k]) for k in ("x", "y", "z")] for r in rows])
quat = np.array([[float(r[k]) for k in ("qx", "qy", "qz", "qw")] for r in rows])
steps = np.linalg.norm(np.diff(xyz, axis=0), axis=1)
qdot = np.abs(np.sum(quat[:-1] * quat[1:], axis=1))
angles = np.degrees(2 * np.arccos(np.clip(qdot, 0, 1)))
with np.load(BASE / "lio-package-margin1s" / "imu.npz", allow_pickle=False) as n:
    imu = {k: n[k] for k in n.files}
with np.load(BASE / "lio-package-margin1s" / "scans.npz", allow_pickle=False) as n:
    scans = {k: n[k] for k in n.files}
origin = float(scans["origin_unix_s"])
starts = origin + scans["frame_start_s"]
ends = origin + scans["frame_end_s"]
imu_t = imu["t_host_unix"]
batch_times, batch_counts = np.unique(imu_t, return_counts=True)

def scan_stats(i):
    a, b = scans["frame_offsets"][i:i + 2]
    r = np.linalg.norm(scans["points"][a:b, :3], axis=1)
    return {"source_frame": int(scans["frame_index"][a]), "frame_start_unix_s": float(starts[i]),
            "frame_end_unix_s": float(ends[i]), "rays": int(b - a),
            "rays_range_ge_100m": int(np.count_nonzero(r >= 100)),
            "rays_range_ge_1000m": int(np.count_nonzero(r >= 1000)),
            "max_range_m": float(np.max(r))}

ranked = []
for j in np.argsort(steps)[::-1][:10]:
    lo, hi = float(t[j]), float(t[j + 1])
    selected = (imu_t >= lo) & (imu_t <= hi)
    nearby = np.argsort(np.abs(ends - hi))[:2]
    bt = batch_times[(batch_times >= lo) & (batch_times <= hi)]
    before = np.searchsorted(batch_times, lo, side="right") - 1
    after = np.searchsorted(batch_times, hi, side="left")
    ranked.append({"pose_indices_zero_based": [int(j), int(j + 1)], "start_unix_s": lo,
        "end_unix_s": hi, "dt_s": hi - lo, "position_step_m": float(steps[j]),
        "orientation_step_deg": float(angles[j]), "imu_host_frames_in_closed_interval": int(selected.sum()),
        "imu_distinct_host_times_in_closed_interval": int(bt.size),
        "gyro_norm_deg_s_min_max_unvalidated": [float(np.linalg.norm(imu["gyro_deg_s"][selected], axis=1).min()), float(np.linalg.norm(imu["gyro_deg_s"][selected], axis=1).max())] if selected.any() else None,
        "accel_norm_m_s2_min_max_unvalidated": [float(np.linalg.norm(imu["accel_m_s2"][selected], axis=1).min()), float(np.linalg.norm(imu["accel_m_s2"][selected], axis=1).max())] if selected.any() else None,
        "preceding_batch_unix_s": float(batch_times[before]) if before >= 0 else None,
        "following_batch_unix_s": float(batch_times[after]) if after < len(batch_times) else None,
        "nearest_scan_ends": [scan_stats(int(i)) for i in nearby]})

new_per_scan = np.diff(np.r_[0, np.searchsorted(imu_t, ends, side="right")])
has_batch = (np.searchsorted(batch_times, t[1:], side="right") - np.searchsorted(batch_times, t[:-1], side="right")) > 0
output = {"status": "DIAGNOSTIC_ONLY", "poses": len(t), "largest_steps": ranked,
    "step_comparison_by_batch_host_time": {name: {"intervals": int(mask.sum()), "median_step_m": float(np.median(steps[mask])), "max_step_m": float(steps[mask].max())} for name, mask in [("batch_present", has_batch), ("no_batch", ~has_batch)] if mask.any()},
    "imu": {"samples": len(imu_t), "distinct_host_times": len(batch_times),
            "duplicate_consecutive_timestamps": int(np.count_nonzero(np.diff(imu_t) == 0)),
            "max_batch_frames": int(batch_counts.max()), "max_gap_between_batch_host_times_s": float(np.diff(batch_times).max()),
            "no_interpolation": True},
    "replay_publisher": {"max_imu_frames_published_before_one_scan": int(new_per_scan.max()),
                         "publisher_qos_depth": 20, "fastlio_subscriber_qos_depth": 10,
                         "received_count": "NOT_RECORDED"},
    "interpretation": "Trajectory and timestamps are diagnostic output and host receive times; no inference about real motion or clock error bounds."}
(Path(__file__).parent / "analysis.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
