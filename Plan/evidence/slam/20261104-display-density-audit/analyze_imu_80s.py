"""Describe raw IMU fields in the first 80 s of radar host-receive time."""
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
imu = ROOT / 'radar_capture_data/na70_sessions/20261104-170341/imu_frames.jsonl'
origin = 1793783021.648630
rows = [json.loads(line) for line in imu.open(encoding='utf-8')]
rows = [r for r in rows if 0 <= float(r['t_unix']) - origin <= 80]
g = np.array([r['gyro'] for r in rows], dtype=float)
e = np.array([r['euler'] for r in rows], dtype=float)
q = np.array([r['quaternion'] for r in rows], dtype=float)
q /= np.linalg.norm(q, axis=1)[:, None]
angles = 2 * np.degrees(np.arccos(np.clip(np.abs(q @ q[0]), 0, 1)))
result = {'selection': '0 <= IMU host Unix receive time - first LiDAR host packet <= 80 s',
          'rows': len(rows), 'host_batches': len(set(r['t_unix'] for r in rows)),
          'relative_first_quaternion_angle_deg_p50_p90_max': np.percentile(angles, [50, 90, 100]).tolist(),
          'gyro_units_assumed': 'deg/s unverified', 'gyro_norm_p50_p90_p99_max': np.percentile(np.linalg.norm(g, axis=1), [50, 90, 99, 100]).tolist(),
          'device_euler_units_assumed': 'deg unverified, no wrap correction', 'euler_min_pitch_roll_yaw': e.min(axis=0).tolist(),
          'euler_max_pitch_roll_yaw': e.max(axis=0).tolist(),
          'limits': 'fused device attitude only; no inferred position or independent attitude truth; host receive not sample time'}
print(json.dumps(result, ensure_ascii=False, indent=2))
