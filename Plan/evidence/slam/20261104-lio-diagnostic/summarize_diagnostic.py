"""Summarize diagnostic output only; no truth, accuracy, or acceptance claim."""
import csv
import json
from pathlib import Path

import numpy as np

root = Path(__file__).resolve().parent
with (root / 'trajectory.csv').open(newline='') as handle:
    rows = list(csv.DictReader(handle))
values = np.asarray([[float(row[k]) for k in ('t_s', 'x', 'y', 'z')] for row in rows])
t, xyz = values[:, 0], values[:, 1:]
dt = np.diff(t)
steps = np.linalg.norm(np.diff(xyz, axis=0), axis=1)
with (root / 'scene' / 'map.ply').open('rb') as handle:
    while handle.readline().strip() != b'end_header':
        pass
    points = np.fromfile(handle, dtype=[('x', '<f4'), ('y', '<f4'), ('z', '<f4'),
                                       ('intensity', 'u1'), ('observations', '<u4'), ('source_frame', '<i4')])
xyz_map = np.column_stack([points[k] for k in ('x', 'y', 'z')])
radius = np.linalg.norm(xyz_map, axis=1)
report = {
    'status': 'DIAGNOSTIC_ONLY',
    'trajectory': {'poses': len(t), 'time_span_s': float(t[-1]-t[0]),
                   'nonpositive_time_steps': int((dt <= 0).sum()),
                   'max_gap_s': float(dt.max()), 'max_step_m': float(steps.max()),
                   'path_length_m': float(steps.sum()),
                   'max_distance_from_first_m': float(np.linalg.norm(xyz-xyz[0], axis=1).max()),
                   'end_distance_from_first_m': float(np.linalg.norm(xyz[-1]-xyz[0]))},
    'assembled_map': {'points': len(points), 'finite_points': int(np.isfinite(xyz_map).all(axis=1).sum()),
                      'distance_from_map_origin_percentiles_m': dict(zip(['p50','p90','p99','max'], map(float, np.percentile(radius, [50,90,99,100])))),
                      'points_over_100m_from_map_origin': int((radius > 100).sum()),
                      'source_frames': int(np.unique(points['source_frame']).size)},
    'ate_rpe_absolute_accuracy': 'NOT_EVALUATED',
    'double_image_plane_consistency': 'NOT_EVALUATED: no independently defined scene ROIs or stable reference planes',
    'limits': ['trajectory and map use unverified identity extrinsic and host-receive time',
               'map-origin radius is not exact range; it only flags extreme spatial extent',
               'point cloud is accumulated by pose interpolation, not a globally optimized map']}
(root / 'diagnostic-stats.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
