"""DIAGNOSTIC ONLY: compare the existing sweep invalid-angle policy in a scene.

This is a candidate interpretation, not a verified manufacturer protocol rule.
It does not modify input or the production scene/observation contract.
"""
from pathlib import Path
import json
import sys
import numpy as np

root = Path(__file__).resolve().parents[4]
out = Path(__file__).resolve().parent
sys.path[:0] = [str(root / 'web'), str(root / 'packages' / 'na70_core'), str(root / 'tools')]
from backend import decoder
from backend.sweeps import INVALID_ANGLE_CODE
from na70_core import scene
from build_na70_scene import load_poses

output = out / 'candidate-sweep-mask-scene'
output.mkdir(exist_ok=False)
pcap = root / 'radar_capture_data/na70_sessions/20261104-170341/raw/lidar.pcap'
pose = load_poses(out / 'trajectory-for-scene.csv')
traj = scene.Trajectory(times=pose['times'], positions=pose['positions'], quaternions=pose['quaternions'], frame='lidar')
builder = scene.MapBuilder(traj, voxel_size=0.1, max_gap_s=0.3)
scanned = decoder.scan_pcap(pcap)
removed = 0
per_frame = []
with pcap.open('rb') as handle:
    for i in range(10, 110):
        frame = scanned.frames[i]
        points = decoder.decode_frame(pcap, frame, source=handle)
        codes = decoder.decode_frame_codes(pcap, frame, source=handle)
        times = np.repeat(frame['packet_capture_times'], 200)
        assert len(points) == len(codes) == len(times)
        # Same rule as Sweep.valid_mask; conflicting with the ray observation contract.
        keep = ((codes[:,0] > 0) & (codes[:,1] != INVALID_ANGLE_CODE) & (codes[:,2] != INVALID_ANGLE_CODE))
        removed += int((~keep).sum())
        per_frame.append({'frame': i, 'removed': int((~keep).sum())})
        builder.add_frame(points[keep], times[keep], frame_index=i)
result = builder.finish()
result.save_ply(output / 'map.ply')
radius = np.linalg.norm(result.points, axis=1)
report = {'status': 'DIAGNOSTIC_ONLY_CANDIDATE_FILTER', 'filter': 'sweeps.valid_mask: distance code > 0 and neither angle code == 0xffff',
          'interpretation': 'UNVERIFIED: conflicts with RayBatch valid_return and direction_known contract; not a manufacturer-verified rule',
          'removed_rays_before_time_filter': removed, 'per_frame_removed': per_frame,
          'map': result.stats, 'origin_radius_percentiles_m': dict(zip(['p50','p90','p99','max'], map(float, np.percentile(radius,[50,90,99,100])))),
          'trajectory_unchanged': True, 'trajectory_and_calibration_validated': False,
          'physical_accuracy': 'NOT_EVALUATED'}
(output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
