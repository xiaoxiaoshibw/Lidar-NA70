"""Trace the farthest saved map voxel to its input ray without rewriting input."""
from pathlib import Path
import json
import sys
import numpy as np

root = Path(__file__).resolve().parents[4]
out = Path(__file__).resolve().parent
sys.path[:0] = [str(root / 'web'), str(root / 'packages' / 'na70_core'), str(root / 'tools')]
from backend import decoder
from na70_core import scene
from build_na70_scene import load_poses

with (out / 'scene/map.ply').open('rb') as handle:
    while handle.readline().strip() != b'end_header':
        pass
    data = np.fromfile(handle, dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('intensity','u1'),('observations','<u4'),('source_frame','<i4')])
xyz = np.column_stack([data[k] for k in ('x','y','z')])
idx = np.argmax(np.linalg.norm(xyz, axis=1))
target = xyz[idx].astype(float)
frame_index = int(data['source_frame'][idx])
pcap = root / 'radar_capture_data/na70_sessions/20261104-170341/raw/lidar.pcap'
frame = decoder.scan_pcap(pcap).frames[frame_index]
points = decoder.decode_frame(pcap, frame)
codes = decoder.decode_frame_codes(pcap, frame)
times = np.repeat(frame['packet_capture_times'], 200)
pose = load_poses(out / 'trajectory-for-scene.csv')
traj = scene.Trajectory(times=pose['times'], positions=pose['positions'], quaternions=pose['quaternions'], frame='lidar')
transformed, _, valid, _ = scene.transform_points(points, times, traj, max_gap_s=0.3)
delta = np.linalg.norm(transformed-target, axis=1)
delta[~valid] = np.inf
ray = int(delta.argmin())
packet, ray_in_packet = divmod(ray, 200)
with pcap.open('rb') as handle:
    offset = int(frame['offsets'][packet]) + ray_in_packet * 7
    handle.seek(offset)
    raw = handle.read(7)
result = {'map_voxel_index': int(idx), 'map_xyz_m': target.tolist(), 'source_frame': frame_index,
          'ray_index_in_frame': ray, 'packet_index_in_frame': packet, 'ray_index_in_packet': ray_in_packet,
          'pcap_absolute_byte_offset': offset, 'raw_ray_hex': raw.hex(), 'raw_codes_distance_pitch_yaw_intensity': codes[ray].astype(int).tolist(),
          'range_code_times_16mm_m': int(codes[ray,0])*0.016, 'decoded_sensor_xyz_m': points[ray,:3].astype(float).tolist(),
          'sensor_xyz_norm_m': float(np.linalg.norm(points[ray,:3])), 'packet_host_time_s': float(times[ray]),
          'reconstructed_map_xyz_m': transformed[ray].tolist(), 'match_error_m': float(delta[ray]),
          'limits': 'Matching reproduces the saved voxel; physical return validity is not established.'}
(out / 'extreme-point-trace.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
